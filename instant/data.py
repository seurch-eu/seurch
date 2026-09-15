"""Static reference data for instant answers.

Everything here is offline data, no network required. Kept in one module so
the lookup tables (HTTP status codes, well-known ports, city time zones,
currency metadata, unit definitions, WMO weather codes) are easy to extend.
"""

from django.utils.translation import gettext_lazy as _

# ---------------------------------------------------------------------------
# HTTP status codes
# ---------------------------------------------------------------------------

HTTP_STATUS = {
    100: ('Continue', 'The server has received the request headers and the client should proceed to send the request body.'),
    101: ('Switching Protocols', 'The requester has asked the server to switch protocols.'),
    102: ('Processing', 'The server has received and is processing the request, but no response is available yet.'),
    103: ('Early Hints', 'Used to return some response headers before the final HTTP message.'),
    200: ('OK', 'The request succeeded. The result depends on the request method.'),
    201: ('Created', 'The request succeeded and a new resource was created as a result.'),
    202: ('Accepted', 'The request has been received but not yet acted upon.'),
    203: ('Non-Authoritative Information', 'The returned metadata is from a local or third-party copy, not the origin server.'),
    204: ('No Content', 'There is no content to send for this request, but the headers may be useful.'),
    205: ('Reset Content', 'Tells the user agent to reset the document which sent this request.'),
    206: ('Partial Content', 'Used when the Range header is sent to return only part of a resource.'),
    207: ('Multi-Status', 'Conveys information about multiple resources, for situations where multiple status codes might be appropriate.'),
    208: ('Already Reported', 'Used inside a DAV propstat response to avoid repeatedly enumerating members of a collection.'),
    226: ('IM Used', 'The server has fulfilled a GET request and the response is a representation of the result of one or more instance-manipulations.'),
    300: ('Multiple Choices', 'The request has more than one possible response; the user agent should choose one.'),
    301: ('Moved Permanently', 'The URL of the requested resource has been changed permanently.'),
    302: ('Found', 'The URI of the requested resource has been changed temporarily.'),
    303: ('See Other', 'The server sent this response to direct the client to get the resource at another URI with a GET request.'),
    304: ('Not Modified', 'Used for caching purposes, the response has not been modified, so the client can use its cached version.'),
    307: ('Temporary Redirect', 'The resource is temporarily at a different URI; the same method must be used.'),
    308: ('Permanent Redirect', 'The resource is permanently at a different URI; the same method must be used.'),
    400: ('Bad Request', 'The server cannot or will not process the request due to a client error (malformed syntax, invalid framing, etc.).'),
    401: ('Unauthorized', 'Authentication is required and has failed or not been provided.'),
    402: ('Payment Required', 'Reserved for future use; sometimes used by APIs for rate or quota limits.'),
    403: ('Forbidden', 'The client does not have access rights to the content; the server understood the request but refuses to authorize it.'),
    404: ('Not Found', 'The server cannot find the requested resource. The URL is not recognized.'),
    405: ('Method Not Allowed', 'The request method is known by the server but is not supported by the target resource.'),
    406: ('Not Acceptable', "The server cannot produce a response matching the list of acceptable values defined in the request's headers."),
    407: ('Proxy Authentication Required', 'Authentication is needed to be done by a proxy.'),
    408: ('Request Timeout', 'The server would like to shut down this unused connection.'),
    409: ('Conflict', 'The request conflicts with the current state of the server.'),
    410: ('Gone', 'The requested content has been permanently deleted from the server.'),
    411: ('Length Required', 'The server rejected the request because the Content-Length header field is not defined.'),
    412: ('Precondition Failed', 'The client has indicated preconditions in its headers which the server does not meet.'),
    413: ('Payload Too Large', 'The request entity is larger than limits defined by the server.'),
    414: ('URI Too Long', 'The URI requested by the client is longer than the server is willing to interpret.'),
    415: ('Unsupported Media Type', 'The media format of the requested data is not supported by the server.'),
    416: ('Range Not Satisfiable', 'The range specified by the Range header field cannot be fulfilled.'),
    417: ('Expectation Failed', 'The expectation indicated by the Expect request header field cannot be met by the server.'),
    418: ("I'm a teapot", 'The server refuses the attempt to brew coffee with a teapot. An April Fools joke from RFC 2324.'),
    421: ('Misdirected Request', 'The request was directed at a server that is not able to produce a response.'),
    422: ('Unprocessable Content', 'The request was well-formed but could not be followed due to semantic errors.'),
    423: ('Locked', 'The resource that is being accessed is locked.'),
    424: ('Failed Dependency', 'The request failed because it depended on another request that failed.'),
    425: ('Too Early', 'The server is unwilling to risk processing a request that might be replayed.'),
    426: ('Upgrade Required', 'The server refuses to perform the request using the current protocol.'),
    428: ('Precondition Required', 'The origin server requires the request to be conditional.'),
    429: ('Too Many Requests', 'The user has sent too many requests in a given amount of time (rate limiting).'),
    431: ('Request Header Fields Too Large', 'The server is unwilling to process the request because its header fields are too large.'),
    451: ('Unavailable For Legal Reasons', 'The user agent requested a resource that cannot legally be provided.'),
    500: ('Internal Server Error', 'The server has encountered a situation it does not know how to handle.'),
    501: ('Not Implemented', 'The request method is not supported by the server and cannot be handled.'),
    502: ('Bad Gateway', 'The server, while acting as a gateway or proxy, received an invalid response from the upstream server.'),
    503: ('Service Unavailable', 'The server is not ready to handle the request, often down for maintenance or overloaded.'),
    504: ('Gateway Timeout', 'The server, while acting as a gateway, did not get a response in time from the upstream server.'),
    505: ('HTTP Version Not Supported', 'The HTTP version used in the request is not supported by the server.'),
    506: ('Variant Also Negotiates', 'The server has an internal configuration error during content negotiation.'),
    507: ('Insufficient Storage', 'The server is unable to store the representation needed to complete the request.'),
    508: ('Loop Detected', 'The server detected an infinite loop while processing the request.'),
    510: ('Not Extended', 'Further extensions to the request are required for the server to fulfil it.'),
    511: ('Network Authentication Required', 'The client needs to authenticate to gain network access.'),
}


def http_category(code):
    """Return ('1xx', label) describing a status code's class."""
    return {
        1: ('1xx', 'Informational'),
        2: ('2xx', 'Success'),
        3: ('3xx', 'Redirection'),
        4: ('4xx', 'Client Error'),
        5: ('5xx', 'Server Error'),
    }.get(code // 100, ('', 'Unknown'))


# ---------------------------------------------------------------------------
# Well-known network ports
# ---------------------------------------------------------------------------

PORTS = {
    20: ('FTP-DATA', 'TCP', 'File Transfer Protocol, data transfer'),
    21: ('FTP', 'TCP', 'File Transfer Protocol, control'),
    22: ('SSH', 'TCP', 'Secure Shell, encrypted remote login and tunnelling'),
    23: ('Telnet', 'TCP', 'Telnet, unencrypted remote login (legacy)'),
    25: ('SMTP', 'TCP', 'Simple Mail Transfer Protocol, email routing'),
    53: ('DNS', 'TCP/UDP', 'Domain Name System, hostname resolution'),
    67: ('DHCP', 'UDP', 'Dynamic Host Configuration Protocol, server'),
    68: ('DHCP', 'UDP', 'Dynamic Host Configuration Protocol, client'),
    69: ('TFTP', 'UDP', 'Trivial File Transfer Protocol'),
    80: ('HTTP', 'TCP', 'Hypertext Transfer Protocol, unencrypted web traffic'),
    110: ('POP3', 'TCP', 'Post Office Protocol v3, email retrieval'),
    119: ('NNTP', 'TCP', 'Network News Transfer Protocol'),
    123: ('NTP', 'UDP', 'Network Time Protocol, clock synchronisation'),
    137: ('NetBIOS', 'UDP', 'NetBIOS Name Service'),
    143: ('IMAP', 'TCP', 'Internet Message Access Protocol, email'),
    161: ('SNMP', 'UDP', 'Simple Network Management Protocol'),
    162: ('SNMP-TRAP', 'UDP', 'SNMP trap notifications'),
    179: ('BGP', 'TCP', 'Border Gateway Protocol, internet routing'),
    194: ('IRC', 'TCP', 'Internet Relay Chat'),
    389: ('LDAP', 'TCP', 'Lightweight Directory Access Protocol'),
    443: ('HTTPS', 'TCP', 'HTTP over TLS/SSL, encrypted web traffic'),
    445: ('SMB', 'TCP', 'Server Message Block, Windows file sharing'),
    465: ('SMTPS', 'TCP', 'SMTP over TLS, secure email submission'),
    514: ('Syslog', 'UDP', 'System logging'),
    520: ('RIP', 'UDP', 'Routing Information Protocol'),
    587: ('SMTP', 'TCP', 'Email message submission (STARTTLS)'),
    631: ('IPP', 'TCP', 'Internet Printing Protocol'),
    636: ('LDAPS', 'TCP', 'LDAP over TLS/SSL'),
    993: ('IMAPS', 'TCP', 'IMAP over TLS/SSL, secure email'),
    995: ('POP3S', 'TCP', 'POP3 over TLS/SSL, secure email'),
    1080: ('SOCKS', 'TCP', 'SOCKS proxy'),
    1194: ('OpenVPN', 'UDP', 'OpenVPN tunnelling'),
    1433: ('MSSQL', 'TCP', 'Microsoft SQL Server'),
    1521: ('Oracle', 'TCP', 'Oracle database listener'),
    1723: ('PPTP', 'TCP', 'Point-to-Point Tunneling Protocol (VPN)'),
    2049: ('NFS', 'TCP', 'Network File System'),
    2082: ('cPanel', 'TCP', 'cPanel web hosting control panel'),
    2375: ('Docker', 'TCP', 'Docker REST API (unencrypted)'),
    2376: ('Docker', 'TCP', 'Docker REST API (TLS)'),
    3000: ('Dev server', 'TCP', 'Common port for Node.js / React / Rails dev servers'),
    3128: ('Squid', 'TCP', 'Squid HTTP proxy'),
    3306: ('MySQL', 'TCP', 'MySQL / MariaDB database'),
    3389: ('RDP', 'TCP', 'Remote Desktop Protocol, Windows remote desktop'),
    5060: ('SIP', 'TCP/UDP', 'Session Initiation Protocol, VoIP signalling'),
    5432: ('PostgreSQL', 'TCP', 'PostgreSQL database'),
    5672: ('AMQP', 'TCP', 'Advanced Message Queuing Protocol, RabbitMQ'),
    5900: ('VNC', 'TCP', 'Virtual Network Computing, remote desktop'),
    6379: ('Redis', 'TCP', 'Redis in-memory data store'),
    6443: ('Kubernetes', 'TCP', 'Kubernetes API server'),
    6667: ('IRC', 'TCP', 'Internet Relay Chat'),
    8000: ('HTTP-alt', 'TCP', 'Common alternative HTTP port (dev servers, Django)'),
    8080: ('HTTP-alt', 'TCP', 'Common alternative HTTP port (proxies, app servers)'),
    8443: ('HTTPS-alt', 'TCP', 'Common alternative HTTPS port'),
    8888: ('HTTP-alt', 'TCP', 'Jupyter and other dev tools'),
    9000: ('HTTP-alt', 'TCP', 'PHP-FPM, SonarQube, MinIO and other services'),
    9090: ('Prometheus', 'TCP', 'Prometheus metrics / web UI'),
    9200: ('Elasticsearch', 'TCP', 'Elasticsearch REST API'),
    11211: ('Memcached', 'TCP', 'Memcached caching system'),
    25565: ('Minecraft', 'TCP', 'Default Minecraft server port'),
    27017: ('MongoDB', 'TCP', 'MongoDB database'),
}


# ---------------------------------------------------------------------------
# WMO weather interpretation codes (Open-Meteo `weather_code`)
# ---------------------------------------------------------------------------

WEATHER_CODES = {
    0: (_('Clear sky'), '☀️'),
    1: (_('Mainly clear'), '\U0001f324️'),
    2: (_('Partly cloudy'), '⛅'),
    3: (_('Overcast'), '☁️'),
    45: (_('Fog'), '\U0001f32b️'),
    48: (_('Depositing rime fog'), '\U0001f32b️'),
    51: (_('Light drizzle'), '\U0001f326️'),
    53: (_('Moderate drizzle'), '\U0001f326️'),
    55: (_('Dense drizzle'), '\U0001f327️'),
    56: (_('Light freezing drizzle'), '\U0001f327️'),
    57: (_('Dense freezing drizzle'), '\U0001f327️'),
    61: (_('Slight rain'), '\U0001f326️'),
    63: (_('Moderate rain'), '\U0001f327️'),
    65: (_('Heavy rain'), '\U0001f327️'),
    66: (_('Light freezing rain'), '\U0001f327️'),
    67: (_('Heavy freezing rain'), '\U0001f327️'),
    71: (_('Slight snow'), '\U0001f328️'),
    73: (_('Moderate snow'), '\U0001f328️'),
    75: (_('Heavy snow'), '❄️'),
    77: (_('Snow grains'), '\U0001f328️'),
    80: (_('Slight rain showers'), '\U0001f326️'),
    81: (_('Moderate rain showers'), '\U0001f327️'),
    82: (_('Violent rain showers'), '⛈️'),
    85: (_('Slight snow showers'), '\U0001f328️'),
    86: (_('Heavy snow showers'), '❄️'),
    95: (_('Thunderstorm'), '⛈️'),
    96: (_('Thunderstorm with slight hail'), '⛈️'),
    99: (_('Thunderstorm with heavy hail'), '⛈️'),
}


def weather_label(code):
    return WEATHER_CODES.get(code, (_('Unknown'), '\U0001f321️'))


# ---------------------------------------------------------------------------
# Currency metadata (covers the currencies the Frankfurter / ECB feed exposes)
# ---------------------------------------------------------------------------

CURRENCIES = {
    'AUD': ('Australian Dollar', '$', '\U0001f1e6\U0001f1fa'),
    'BGN': ('Bulgarian Lev', 'лв', '\U0001f1e7\U0001f1ec'),
    'BRL': ('Brazilian Real', 'R$', '\U0001f1e7\U0001f1f7'),
    'CAD': ('Canadian Dollar', '$', '\U0001f1e8\U0001f1e6'),
    'CHF': ('Swiss Franc', 'Fr', '\U0001f1e8\U0001f1ed'),
    'CNY': ('Chinese Yuan', '¥', '\U0001f1e8\U0001f1f3'),
    'CZK': ('Czech Koruna', 'Kč', '\U0001f1e8\U0001f1ff'),
    'DKK': ('Danish Krone', 'kr', '\U0001f1e9\U0001f1f0'),
    'EUR': ('Euro', '€', '\U0001f1ea\U0001f1fa'),
    'GBP': ('British Pound', '£', '\U0001f1ec\U0001f1e7'),
    'HKD': ('Hong Kong Dollar', '$', '\U0001f1ed\U0001f1f0'),
    'HUF': ('Hungarian Forint', 'Ft', '\U0001f1ed\U0001f1fa'),
    'IDR': ('Indonesian Rupiah', 'Rp', '\U0001f1ee\U0001f1e9'),
    'ILS': ('Israeli New Shekel', '₪', '\U0001f1ee\U0001f1f1'),
    'INR': ('Indian Rupee', '₹', '\U0001f1ee\U0001f1f3'),
    'ISK': ('Icelandic Króna', 'kr', '\U0001f1ee\U0001f1f8'),
    'JPY': ('Japanese Yen', '¥', '\U0001f1ef\U0001f1f5'),
    'KRW': ('South Korean Won', '₩', '\U0001f1f0\U0001f1f7'),
    'MXN': ('Mexican Peso', '$', '\U0001f1f2\U0001f1fd'),
    'MYR': ('Malaysian Ringgit', 'RM', '\U0001f1f2\U0001f1fe'),
    'NOK': ('Norwegian Krone', 'kr', '\U0001f1f3\U0001f1f4'),
    'NZD': ('New Zealand Dollar', '$', '\U0001f1f3\U0001f1ff'),
    'PHP': ('Philippine Peso', '₱', '\U0001f1f5\U0001f1ed'),
    'PLN': ('Polish Złoty', 'zł', '\U0001f1f5\U0001f1f1'),
    'RON': ('Romanian Leu', 'lei', '\U0001f1f7\U0001f1f4'),
    'SEK': ('Swedish Krona', 'kr', '\U0001f1f8\U0001f1ea'),
    'SGD': ('Singapore Dollar', '$', '\U0001f1f8\U0001f1ec'),
    'THB': ('Thai Baht', '฿', '\U0001f1f9\U0001f1ed'),
    'TRY': ('Turkish Lira', '₺', '\U0001f1f9\U0001f1f7'),
    'USD': ('US Dollar', '$', '\U0001f1fa\U0001f1f8'),
    'ZAR': ('South African Rand', 'R', '\U0001f1ff\U0001f1e6'),
}

# Currency symbols → ISO code (defaults chosen for the most common usage).
CURRENCY_SYMBOLS = {
    '$': 'USD', 'US$': 'USD', 'A$': 'AUD', 'C$': 'CAD', 'NZ$': 'NZD', 'HK$': 'HKD',
    '€': 'EUR', '£': 'GBP', '¥': 'JPY', '₹': 'INR', '₩': 'KRW', 'R$': 'BRL',
    '₺': 'TRY', '₪': 'ILS', 'zł': 'PLN', '฿': 'THB', '₱': 'PHP', 'Kč': 'CZK',
    'Ft': 'HUF', 'Rp': 'IDR', 'RM': 'MYR',
}

# Spelled-out currency words → ISO code.
CURRENCY_WORDS = {
    'dollar': 'USD', 'dollars': 'USD', 'usd': 'USD', 'buck': 'USD', 'bucks': 'USD',
    'euro': 'EUR', 'euros': 'EUR', 'eur': 'EUR',
    'pound': 'GBP', 'pounds': 'GBP', 'sterling': 'GBP', 'quid': 'GBP', 'gbp': 'GBP',
    'yen': 'JPY', 'jpy': 'JPY',
    'yuan': 'CNY', 'renminbi': 'CNY', 'rmb': 'CNY', 'cny': 'CNY',
    'rupee': 'INR', 'rupees': 'INR', 'inr': 'INR',
    'won': 'KRW', 'krw': 'KRW',
    'real': 'BRL', 'reais': 'BRL', 'brl': 'BRL',
    'franc': 'CHF', 'francs': 'CHF', 'chf': 'CHF',
    'ruble': 'RUB', 'rubles': 'RUB',
    'peso': 'MXN', 'pesos': 'MXN',
    'rand': 'ZAR', 'zar': 'ZAR',
    'lira': 'TRY', 'try': 'TRY',
    'zloty': 'PLN', 'pln': 'PLN',
    'baht': 'THB', 'thb': 'THB',
    # localized spellings (fr / de / es / it / pt / nl)
    'dólar': 'USD', 'dólares': 'USD', 'dolar': 'USD', 'dolares': 'USD',
    'dollaro': 'USD', 'dollari': 'USD',
    'livre': 'GBP', 'livres': 'GBP', 'libra': 'GBP', 'libras': 'GBP',
    'sterlina': 'GBP', 'sterline': 'GBP', 'pfund': 'GBP',
    'iene': 'JPY', 'yenes': 'JPY',
    'rupia': 'INR', 'rupias': 'INR', 'rupie': 'INR',
    'franco': 'CHF', 'franchi': 'CHF', 'francos': 'CHF', 'franken': 'CHF',
}


def currency_meta(code):
    return CURRENCIES.get(code, (code, '', ''))


# ---------------------------------------------------------------------------
# City / region → IANA time zone (for the world-clock instant answer)
# ---------------------------------------------------------------------------
#
# Keys are lowercase. Includes major cities, a few countries with a single
# dominant zone, common nicknames, and time-zone abbreviations mapped to a
# representative IANA zone (so DST is handled correctly).

CITY_TZ = {
    # --- abbreviations / generic ---
    'utc': 'UTC', 'gmt': 'UTC', 'zulu': 'UTC',
    'est': 'America/New_York', 'edt': 'America/New_York',
    'cst': 'America/Chicago', 'cdt': 'America/Chicago',
    'mst': 'America/Denver', 'mdt': 'America/Denver',
    'pst': 'America/Los_Angeles', 'pdt': 'America/Los_Angeles',
    'cet': 'Europe/Paris', 'cest': 'Europe/Paris',
    'eet': 'Europe/Athens', 'bst': 'Europe/London',
    'ist': 'Asia/Kolkata', 'jst': 'Asia/Tokyo', 'kst': 'Asia/Seoul',
    'aest': 'Australia/Sydney', 'nzst': 'Pacific/Auckland',
    # --- Europe ---
    'london': 'Europe/London', 'paris': 'Europe/Paris', 'berlin': 'Europe/Berlin',
    'madrid': 'Europe/Madrid', 'barcelona': 'Europe/Madrid', 'rome': 'Europe/Rome',
    'milan': 'Europe/Rome', 'amsterdam': 'Europe/Amsterdam', 'brussels': 'Europe/Brussels',
    'vienna': 'Europe/Vienna', 'zurich': 'Europe/Zurich', 'geneva': 'Europe/Zurich',
    'lisbon': 'Europe/Lisbon', 'dublin': 'Europe/Dublin', 'munich': 'Europe/Berlin',
    'frankfurt': 'Europe/Berlin', 'hamburg': 'Europe/Berlin', 'prague': 'Europe/Prague',
    'warsaw': 'Europe/Warsaw', 'budapest': 'Europe/Budapest', 'athens': 'Europe/Athens',
    'stockholm': 'Europe/Stockholm', 'oslo': 'Europe/Oslo', 'copenhagen': 'Europe/Copenhagen',
    'helsinki': 'Europe/Helsinki', 'moscow': 'Europe/Moscow', 'kyiv': 'Europe/Kyiv',
    'kiev': 'Europe/Kyiv', 'istanbul': 'Europe/Istanbul', 'bucharest': 'Europe/Bucharest',
    'belgrade': 'Europe/Belgrade', 'sofia': 'Europe/Sofia', 'reykjavik': 'Atlantic/Reykjavik',
    'edinburgh': 'Europe/London', 'manchester': 'Europe/London',
    # --- Americas ---
    'new york': 'America/New_York', 'nyc': 'America/New_York', 'boston': 'America/New_York',
    'washington': 'America/New_York', 'miami': 'America/New_York', 'atlanta': 'America/New_York',
    'toronto': 'America/Toronto', 'montreal': 'America/Toronto', 'ottawa': 'America/Toronto',
    'chicago': 'America/Chicago', 'dallas': 'America/Chicago', 'houston': 'America/Chicago',
    'denver': 'America/Denver', 'phoenix': 'America/Phoenix',
    'los angeles': 'America/Los_Angeles', 'la': 'America/Los_Angeles',
    'san francisco': 'America/Los_Angeles', 'sf': 'America/Los_Angeles',
    'seattle': 'America/Los_Angeles', 'las vegas': 'America/Los_Angeles',
    'vancouver': 'America/Vancouver', 'mexico city': 'America/Mexico_City',
    'sao paulo': 'America/Sao_Paulo', 'são paulo': 'America/Sao_Paulo',
    'rio de janeiro': 'America/Sao_Paulo', 'rio': 'America/Sao_Paulo',
    'buenos aires': 'America/Argentina/Buenos_Aires', 'lima': 'America/Lima',
    'bogota': 'America/Bogota', 'santiago': 'America/Santiago',
    # --- Asia ---
    'tokyo': 'Asia/Tokyo', 'osaka': 'Asia/Tokyo', 'kyoto': 'Asia/Tokyo',
    'seoul': 'Asia/Seoul', 'beijing': 'Asia/Shanghai', 'shanghai': 'Asia/Shanghai',
    'hong kong': 'Asia/Hong_Kong', 'taipei': 'Asia/Taipei', 'singapore': 'Asia/Singapore',
    'bangkok': 'Asia/Bangkok', 'jakarta': 'Asia/Jakarta', 'manila': 'Asia/Manila',
    'kuala lumpur': 'Asia/Kuala_Lumpur', 'hanoi': 'Asia/Ho_Chi_Minh',
    'ho chi minh city': 'Asia/Ho_Chi_Minh', 'mumbai': 'Asia/Kolkata',
    'delhi': 'Asia/Kolkata', 'new delhi': 'Asia/Kolkata', 'bangalore': 'Asia/Kolkata',
    'bengaluru': 'Asia/Kolkata', 'kolkata': 'Asia/Kolkata', 'chennai': 'Asia/Kolkata',
    'karachi': 'Asia/Karachi', 'lahore': 'Asia/Karachi', 'islamabad': 'Asia/Karachi',
    'dhaka': 'Asia/Dhaka', 'dubai': 'Asia/Dubai', 'abu dhabi': 'Asia/Dubai',
    'doha': 'Asia/Qatar', 'riyadh': 'Asia/Riyadh', 'tehran': 'Asia/Tehran',
    'tel aviv': 'Asia/Jerusalem', 'jerusalem': 'Asia/Jerusalem', 'baghdad': 'Asia/Baghdad',
    # --- Africa ---
    'cairo': 'Africa/Cairo', 'lagos': 'Africa/Lagos', 'nairobi': 'Africa/Nairobi',
    'johannesburg': 'Africa/Johannesburg', 'cape town': 'Africa/Johannesburg',
    'casablanca': 'Africa/Casablanca', 'accra': 'Africa/Accra', 'addis ababa': 'Africa/Addis_Ababa',
    # --- Oceania ---
    'sydney': 'Australia/Sydney', 'melbourne': 'Australia/Melbourne',
    'brisbane': 'Australia/Brisbane', 'perth': 'Australia/Perth',
    'adelaide': 'Australia/Adelaide', 'auckland': 'Pacific/Auckland',
    'wellington': 'Pacific/Auckland', 'honolulu': 'Pacific/Honolulu',
    # --- countries with one dominant zone ---
    'uk': 'Europe/London', 'united kingdom': 'Europe/London', 'england': 'Europe/London',
    'ireland': 'Europe/Dublin', 'france': 'Europe/Paris', 'germany': 'Europe/Berlin',
    'spain': 'Europe/Madrid', 'italy': 'Europe/Rome', 'netherlands': 'Europe/Amsterdam',
    'belgium': 'Europe/Brussels', 'switzerland': 'Europe/Zurich', 'austria': 'Europe/Vienna',
    'portugal': 'Europe/Lisbon', 'poland': 'Europe/Warsaw', 'greece': 'Europe/Athens',
    'sweden': 'Europe/Stockholm', 'norway': 'Europe/Oslo', 'denmark': 'Europe/Copenhagen',
    'finland': 'Europe/Helsinki', 'ukraine': 'Europe/Kyiv', 'turkey': 'Europe/Istanbul',
    'japan': 'Asia/Tokyo', 'south korea': 'Asia/Seoul', 'korea': 'Asia/Seoul',
    'china': 'Asia/Shanghai', 'india': 'Asia/Kolkata', 'thailand': 'Asia/Bangkok',
    'singapore city': 'Asia/Singapore', 'indonesia': 'Asia/Jakarta',
    'philippines': 'Asia/Manila', 'vietnam': 'Asia/Ho_Chi_Minh', 'pakistan': 'Asia/Karachi',
    'bangladesh': 'Asia/Dhaka', 'uae': 'Asia/Dubai', 'qatar': 'Asia/Qatar',
    'saudi arabia': 'Asia/Riyadh', 'iran': 'Asia/Tehran', 'israel': 'Asia/Jerusalem',
    'egypt': 'Africa/Cairo', 'nigeria': 'Africa/Lagos', 'kenya': 'Africa/Nairobi',
    'south africa': 'Africa/Johannesburg', 'morocco': 'Africa/Casablanca',
    'new zealand': 'Pacific/Auckland',
    # --- localized city names (exonyms) ---
    'londres': 'Europe/London', 'londra': 'Europe/London',
    'parís': 'Europe/Paris', 'parigi': 'Europe/Paris',
    'roma': 'Europe/Rome', 'milano': 'Europe/Rome', 'venezia': 'Europe/Rome',
    'firenze': 'Europe/Rome', 'napoli': 'Europe/Rome', 'torino': 'Europe/Rome',
    'lisboa': 'Europe/Lisbon', 'lisbonne': 'Europe/Lisbon', 'lisbona': 'Europe/Lisbon',
    'münchen': 'Europe/Berlin', 'muenchen': 'Europe/Berlin', 'múnich': 'Europe/Berlin',
    'monaco di baviera': 'Europe/Berlin', 'colonia': 'Europe/Berlin',
    'wien': 'Europe/Vienna', 'vienne': 'Europe/Vienna', 'viena': 'Europe/Vienna',
    'bruxelles': 'Europe/Brussels', 'brussel': 'Europe/Brussels', 'bruselas': 'Europe/Brussels',
    'genève': 'Europe/Zurich', 'ginevra': 'Europe/Zurich', 'ginebra': 'Europe/Zurich',
    'zürich': 'Europe/Zurich', 'zurigo': 'Europe/Zurich',
    'moscou': 'Europe/Moscow', 'moscú': 'Europe/Moscow', 'mosca': 'Europe/Moscow',
    'moskau': 'Europe/Moscow', 'praga': 'Europe/Prague', 'prag': 'Europe/Prague',
    'varsovie': 'Europe/Warsaw', 'varsovia': 'Europe/Warsaw', 'varsavia': 'Europe/Warsaw',
    'warschau': 'Europe/Warsaw', 'athènes': 'Europe/Athens', 'atenas': 'Europe/Athens',
    'atene': 'Europe/Athens', 'athen': 'Europe/Athens', 'estambul': 'Europe/Istanbul',
    'copenhague': 'Europe/Copenhagen', 'copenaghen': 'Europe/Copenhagen', 'kopenhagen': 'Europe/Copenhagen',
    'nueva york': 'America/New_York', 'nova iorque': 'America/New_York',
    'tokio': 'Asia/Tokyo', 'pékin': 'Asia/Shanghai', 'pekín': 'Asia/Shanghai',
    'peking': 'Asia/Shanghai', 'pechino': 'Asia/Shanghai', 'bombay': 'Asia/Kolkata',
    'le caire': 'Africa/Cairo', 'el cairo': 'Africa/Cairo', 'il cairo': 'Africa/Cairo',
    'kairo': 'Africa/Cairo', 'singapour': 'Asia/Singapore', 'singapur': 'Asia/Singapore',
    'sidney': 'Australia/Sydney',
}
