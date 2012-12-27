from couchpotato.core.helpers.encoding import tryUrlencode
from couchpotato.core.helpers.rss import RSS
from couchpotato.core.helpers.variable import cleanHost, splitString
from couchpotato.core.logger import CPLog
from couchpotato.core.providers.base import ResultList
from couchpotato.core.providers.nzb.base import NZBProvider
from couchpotato.environment import Env
from dateutil.parser import parse
from urllib2 import HTTPError
from urlparse import urlparse
import time
import traceback

log = CPLog(__name__)


class Newznab(NZBProvider, RSS):

    urls = {
        'download': 'get&id=%s',
        'detail': 'details&id=%s',
        'search': 'movie',
    }

    limits_reached = {}

    http_time_between_calls = 1 # Seconds

    def getCatId(self, host, identifier):
        '''
        Override couchpotato.core.providers.YarrProvider
        Parse from host['cat_ids']
        '''
        log.debug('CatIDs in conf: %s', host['cat_ids'] )

        for cats in host['cat_ids'].split('|'):
            split_ids = cats.split(';')
            ids = split_ids[ : len( split_ids ) -1 ]
            qualities = split_ids[ len( split_ids ) -1 ].split(':')
            if identifier in qualities:
                return ids

        return [ host['cat_backup'] ]

    def feed(self):

        hosts = self.getHosts()

        results = []
        for host in hosts:
            result = self.singleFeed(host)

            if result:
                results.extend(result)

        return results

    def singleFeed(self, host):

        results = []
        if self.isDisabled(host):
            return results

        arguments = tryUrlencode({
            't': host['cat_backup'],
            'r': host['api_key'],
            'i': 58,
        })
        url = '%s?%s' % (cleanHost(host['host']) + 'rss', arguments)
        cache_key = 'newznab.%s.feed.%s' % (host['host'], arguments)

        results = self.createItems(url, cache_key, host, for_feed = True)

        return results

    def search(self, movie, quality):
        hosts = self.getHosts()

        results = ResultList(self, movie, quality, imdb_result = True)

        for host in hosts:
            if self.isDisabled(host):
                continue

            self._searchOnHost(host, movie, quality, results)

        return results

    def _searchOnHost(self, host, movie, quality, results):

        cat_id = self.getCatId(host, quality['identifier'])
        arguments = tryUrlencode({
            'imdbid': movie['library']['identifier'].replace('tt', ''),
            'apikey': host['api_key'],
            'extended': 1
        })
        url = '%s&%s' % (self.getUrl(host['host'], self.urls['search']), arguments)

        nzbs = self.getRSSData(url, cache_timeout = 1800, headers = {'User-Agent': Env.getIdentifier()})

        for nzb in nzbs:

            date = None
            for item in nzb:
                if item.attrib.get('name') == 'usenetdate':
                    date = item.attrib.get('value')
                    break

            if not date:
                date = self.getTextElement(nzb, 'pubDate')

            nzb_id = self.getTextElement(nzb, 'guid').split('/')[-1:].pop()
            name = self.getTextElement(nzb, 'title')

            if not name:
                continue

            results.append({
                'id': nzb_id,
                'provider_extra': host['host'],
                'name': self.getTextElement(nzb, 'title'),
                'age': self.calculateAge(int(time.mktime(parse(date).timetuple()))),
                'size': int(self.getElement(nzb, 'enclosure').attrib['length']) / 1024 / 1024,
                'url': (self.getUrl(host['host'], self.urls['download']) % nzb_id) + self.getApiExt(host),
                'detail_url': '%sdetails/%s' % (cleanHost(host['host']), nzb_id),
                'content': self.getTextElement(nzb, 'description'),
            })

    def getHosts(self):

        uses = splitString(str(self.conf('use')))
        hosts = splitString(self.conf('host'))
        api_keys = splitString(self.conf('api_key'))
        cat_ids = splitString(self.conf('cat_ids'))
        cat_backups = splitString(self.conf('cat_backup'))

        list = []
        for nr in range(len(hosts)):
            list.append({
                'use': uses[nr],
                'host': hosts[nr],
                'api_key': api_keys[nr],
                'cat_ids': cat_ids[nr],
                'cat_backup': cat_backups[nr]
            })

        return list

    def belongsTo(self, url, provider = None):

        hosts = self.getHosts()

        for host in hosts:
            result = super(Newznab, self).belongsTo(url, host = host['host'], provider = provider)
            if result:
                return result

    def getUrl(self, host, type):
        return cleanHost(host) + 'api?t=' + type

    def isDisabled(self, host):
        return not self.isEnabled(host)

    def isEnabled(self, host):
        return NZBProvider.isEnabled(self) and host['host'] and host['api_key'] and int(host['use'])

    def getApiExt(self, host):
        return '&apikey=%s' % host['api_key']

    def download(self, url = '', nzb_id = ''):
        host = urlparse(url).hostname

        if self.limits_reached.get(host):
            # Try again in 3 hours
            if self.limits_reached[host] > time.time() - 10800:
                return 'try_next'

        try:
            data = self.urlopen(url, show_error = False)
            self.limits_reached[host] = False
            return data
        except HTTPError, e:
            if e.code == 503:
                response = e.read().lower()
                if 'maximum api' in response or 'download limit' in response:
                    if not self.limits_reached.get(host):
                        log.error('Limit reached for newznab provider: %s', host)
                    self.limits_reached[host] = time.time()
                    return 'try_next'

            log.error('Failed download from %s', (host, traceback.format_exc()))

        return 'try_next'
