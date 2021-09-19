import time
import requests
import telegraph
import random
import fasteners
import threading
from typing import List, Union
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter

import log
import env

logger = log.getLogger('RSStT.tgraph')


class Session(requests.Session):
    def request(self, *args, **kwargs):
        kwargs.setdefault('timeout', (5, 5))
        kwargs.setdefault('proxies', {'all': env.TELEGRAM_PROXY})
        return super().request(*args, **kwargs)


class Telegraph(telegraph.Telegraph):
    def __init__(self, access_token=None):
        self._lock = fasteners.ReaderWriterLock()  # rwlock: wait if exceed flood control
        self._rlock = threading.RLock()  # lock: only one request can be sent at the same time

        self._telegraph = telegraph.api.TelegraphApi(access_token)
        self._telegraph.session = Session()
        self._telegraph.session.mount('https://', HTTPAdapter(max_retries=1))
        self.retries = 0
        self.last_run = time.time()

    def create_page(self, *args, **kwargs):
        if self.retries >= 3:
            self.retries = 0
            raise OverflowError

        if self.retries >= 1:
            logger.info('Retrying...')

        try:
            page = self._create_page(*args, **kwargs)
            self.retries = 0
            return page
        except telegraph.TelegraphException as e:
            e_msg = str(e)
            if e_msg.startswith('FLOOD_WAIT_'):  # exceed flood control
                retry_after = int(e_msg.split('_')[-1])
                logger.warning(f'Flood control exceeded. Retry in {retry_after}.0 seconds')
                self.retries += 1
                if not self._lock.owner == self._lock.WRITER:  # if not already blocking
                    with self._lock.write_lock():  # block any other tries
                        if retry_after >= 60:
                            # create a now account if retry_after sucks
                            self.create_account(short_name='RSStT', author_name='Generated by RSStT',
                                                author_url='https://github.com/Rongronggg9/RSS-to-Telegram-Bot')
                            logger.warning('Wanna let me wait? No way! Created a new Telegraph account.')
                        else:
                            time.sleep(retry_after + 1)
                return self.create_page(*args, **kwargs)
            else:
                raise e

    @fasteners.read_locked
    def _create_page(self, *args, **kwargs):
        with self._rlock:
            time.sleep(max(1 - (time.time() - self.last_run), 0))  # avoid exceeding flood control
            ret = super().create_page(*args, **kwargs)
            self.last_run = time.time()
            return ret


class API:
    def __init__(self, tokens: Union[str, List[str]]):
        if isinstance(tokens, str):
            tokens = [tokens]
        self._accounts = []
        for token in tokens:
            token = token.strip()
            account = Telegraph(token)
            try:
                if len(token) != 60:  # must be an invalid token
                    logger.warning('Telegraph API token may be invalid, create one instead.')
                    account.create_account(short_name='RSStT', author_name='Generated by RSStT',
                                           author_url='https://github.com/Rongronggg9/RSS-to-Telegram-Bot')
                account.get_account_info()
                self._accounts.append(account)
            except telegraph.TelegraphException as e:
                logger.warning('Telegraph API token may be invalid, create one instead: ' + str(e))
                try:
                    account.create_account(short_name='RSStT', author_name='Generated by RSStT',
                                           author_url='https://github.com/Rongronggg9/RSS-to-Telegram-Bot')
                    self._accounts.append(account)
                except Exception as e:
                    logger.warning('Cannot set up one of Telegraph accounts: ' + str(e), exc_info=e)
            except Exception as e:
                logger.warning('Cannot set up one of Telegraph accounts: ' + str(e), exc_info=e)

    @property
    def valid(self):
        return bool(self._accounts)

    @property
    def count(self):
        return len(self._accounts)

    def create_page(self, *args, **kwargs):
        if not self._accounts:
            raise telegraph.TelegraphException('Telegraph token no set!')

        return random.choice(self._accounts).create_page(*args, **kwargs)  # choose an account randomly


api = None
if env.TELEGRAPH_TOKEN:
    api = API(env.TELEGRAPH_TOKEN.split(','))
    if not api.valid:
        logger.error('Cannot set up Telegraph, fallback to non-Telegraph mode.')
        api = None

TELEGRAPH_ALLOWED_TAGS = {
    'a', 'aside', 'b', 'blockquote', 'br', 'code', 'em', 'figcaption', 'figure',
    'h3', 'h4', 'hr', 'i', 'iframe', 'img', 'li', 'ol', 'p', 'pre', 's',
    'strong', 'u', 'ul', 'video'
}


class TelegraphIfy:
    # if Telegraph account is not set but telegraph_ify is called, let it raise exception heartily :-)
    max_concurrency = api.count if api else 10

    # limit Telegraph_ify concurrency so that we can increase the concurrency of post.generate_message
    _semaphore = threading.BoundedSemaphore(max_concurrency)

    def __init__(self, xml: str = None, title: str = None, link: str = None, feed_title: str = None,
                 author: str = None):
        if not api:
            raise telegraph.TelegraphException('Telegraph token no set!')

        soup = BeautifulSoup(xml, 'lxml')

        for tag in soup.find_all(recursive=True):
            if tag.name not in TELEGRAPH_ALLOWED_TAGS:
                tag.replaceWithChildren()

        if feed_title:
            self.telegraph_author = f"{feed_title}"
            if author and author not in feed_title:
                self.telegraph_author += f' ({author})'
            self.telegraph_author_url = link if link else ''
        else:
            self.telegraph_author = 'Generated by RSStT'
            self.telegraph_author_url = 'https://github.com/Rongronggg9/RSS-to-Telegram-Bot'

        self.telegraph_title = title if title else 'Generated by RSStT'
        self.telegraph_html_content = (str(soup) +
                                       "<br><br>Generated by "
                                       "<a href='https://github.com/Rongronggg9/RSS-to-Telegram-Bot'>RSStT</a>. "
                                       "The copyright belongs to the source site." +
                                       f"<br><br><a href='{link}'>Source</a>" if link else '')

    def telegraph_ify(self):
        with self._semaphore:
            telegraph_page = api.create_page(title=self.telegraph_title[:256],
                                             html_content=self.telegraph_html_content,
                                             author_name=self.telegraph_author[:128],
                                             author_url=self.telegraph_author_url[:512])
        return telegraph_page['url']
