#!/usr/bin/env python3

import argparse
import atexit
import datetime
import os
import psutil
import re
import subprocess
import sys
import time
import urllib

import requests
import scrapy.crawler
import scrapy.exceptions
import scrapy.spiders

import sharkscout


# SharkScout Scrapy spider
class Spider(scrapy.spiders.Spider):
    name = 'spider'
    custom_settings = {
        'HTTPERROR_ALLOW_ALL': True  # let parse() deal with them
    }
    url_regex = []
    closed_reason = None

    def __init__(self, *args, **kwargs):
        self.__class__.closed_reason = None

        if 'start_url' in kwargs:
            self.__class__.start_urls = [kwargs.pop('start_url')]
        self.__class__.allowed_domains = [urllib.parse.urlparse(u).hostname for u in self.__class__.start_urls]

        if 'url_regex' in kwargs:
            self.__class__.url_regex = kwargs.pop('url_regex')

        super(self.__class__, self).__init__(*args, **kwargs)

    def parse(self, response):
        # Stop on any error
        if response.status >= 400:
            raise scrapy.exceptions.CloseSpider(int(response.status))

        urls = response.xpath('//*/@href').extract()

        # Prevent urllib.parse.urlparse() from being dumb...
        urls = [('http://' if 'www' in u else '') + u for u in urls]
        urls = [('http://' if u.endswith(('.com','.net','.org')) else '') + u for u in urls]

        # Actually obey allowed_domains...
        if self.__class__.allowed_domains:
            urls = [u for u in urls if urllib.parse.urlparse(u) not in self.__class__.allowed_domains + [None]]

        # URL massaging
        for idx, url in enumerate(urls):
            url = re.sub(r':///+', '://', url)
            url = response.urljoin(url)
            url = url.rstrip('/')
            urls[idx] = url

        # Yield logic
        for url in urls:
            if self.__class__.url_regex:
                for regex in self.__class__.url_regex:
                    if re.search(regex, url):
                        yield scrapy.Request(url)
                        continue
            else:
                yield scrapy.Request(url)

    # Remember why the spider stopped
    def closed(self, reason):
        print('closed', reason)
        self.__class__.closed_reason = reason


if __name__ == '__main__':
    parser = argparse.ArgumentParser(prog=__file__)
    parser.add_argument('params', nargs='+')
    known, unknown = parser.parse_known_args()
    params = known.params + unknown
    if params[0].endswith('.py'):
        params.insert(0, sys.executable)

    # Start SharkScout
    null = open(os.devnull, 'w')
    server = subprocess.Popen(params, stdout=null, stderr=subprocess.STDOUT)

    # Stop SharkScout on quit
    @atexit.register
    def goodbye():
        # Terminate any processes with an open port
        procs = [psutil.Process(sharkscout.Util.pid_of_port(p)) for p in sharkscout.Util.pid_tree_ports(server.pid)]
        for proc in procs:
            proc.terminate()
        psutil.wait_procs(procs, timeout=5)
        null.close()

    # Wait for the web server to start
    ports = []
    while server.poll() is None:
        ports = sharkscout.Util.pid_tree_ports(server.pid)
        if len(ports) > 1:  # both mongod and web server
            break
        time.sleep(0.1)
    if not ports:
        print('SharkScout failed to start the web server')
        sys.exit(1)

    port_found = False
    for port in ports:
        # Basic HTTP root test
        url = 'http://localhost:' + str(port)
        try:
            requests.get(url).raise_for_status()
            port_found = True
        except requests.exceptions.RequestException as e:
            continue

        # Crawler smoke test
        crawler = scrapy.crawler.CrawlerProcess({
            'USER_AGENT': 'Mozilla/5.0'
        })
        year = str(datetime.date.today().year)
        crawler.crawl(Spider(start_url=url, url_regex=[url + u for u in [
            # Events, events + year, event
            r'/events$',
            r'/events/[0-9]+$',
            r'/event/' + year + '[^/]+$',
            # Teams, team
            r'/teams$',
            r'/teams/[0-9]+$',
            r'/team/[^/]+$',
            # Scouting: pit, match, match + key, match + key + team
            r'/scout/pit/[^/]+$',
            r'/scout/match/[^/]+$',
            r'/scout/match/[^/]+/[^/]+[a-z]1$',
            r'/scout/match/[^/]+/[^/]+[a-z]1/[^/]+$'
        ]]))
        crawler.start()
        if isinstance(Spider.closed_reason, int):
            sys.exit(1)

    if not port_found:
        sys.exit(1)
