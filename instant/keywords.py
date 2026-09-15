"""Multilingual trigger vocabulary for instant-answer detection.

Covers the languages the rest of the app supports: English, French, German,
Spanish, Italian, Portuguese and Dutch. All entries are lowercase; handlers
build case-insensitive regex alternations from them via :func:`alt`.

Keeping every localized keyword in one place means a handler's logic stays the
same across languages, only the vocabulary lists grow.
"""

import re


def alt(words):
    """Regex alternation of *words*, longest first (so prefixes don't win), escaped."""
    return '|'.join(re.escape(w) for w in sorted(set(words), key=len, reverse=True))


# Connectors meaning "to / into / in / as" (X <conn> Y), currency, units, base.
CONNECTORS = [
    'to', 'into', 'in', 'as',          # en
    'en', 'vers', 'à',                 # fr
    'zu', 'nach',                      # de
    'a',                               # es / it
    'para', 'em',                      # pt
    'naar',                            # nl
]

# "of / for", hash ("md5 of x"), percent ("15% of 200").
OF = ['of', 'for', 'de', 'da', 'do', 'di', 'von', 'van', 'pour', 'für', 'fuer', 'per']

# Leading articles to strip before matching ("el tiempo en madrid").
ARTICLES = ['the', 'el', 'la', 'le', 'les', 'los', 'las', 'il', 'lo', 'o', 'os',
            'het', 'de', 'der', 'die', 'das', "l'", 'a']

# Prepositions after a trigger word ("weather in X", "heure à X").
PREP = ['in', 'at', 'for', 'of', 'à', 'a', 'en', 'de', 'du', 'em', 'no', 'na',
        'di', 'voor', 'für', 'fuer', 'pour']

CONVERT = ['convert', 'convertir', 'convertire', 'converter', 'umrechnen',
           'omrekenen', 'conversion', 'conversione', 'conversión', 'conversão']

# Radix names per base.
BASE_NAMES = {
    16: ['hex', 'hexadecimal', 'hexadécimal', 'hexadezimal', 'esadecimale', 'hexadecimaal'],
    10: ['dec', 'decimal', 'décimal', 'dezimal', 'decimale', 'decimaal'],
    2: ['bin', 'binary', 'binaire', 'binär', 'binar', 'binario', 'binário', 'binair'],
    8: ['oct', 'octal', 'oktal', 'ottale', 'octaal'],
}

# Weather trigger words. Note: tiempo/tempo mean weather here; time words live
# in TIME below, so "tiempo en madrid" → weather and "hora en madrid" → clock.
WEATHER = [
    'weather', 'forecast',                                  # en
    'météo', 'meteo',                                       # fr / it
    'wetter', 'wettervorhersage', 'vorhersage',             # de
    'tiempo', 'clima', 'pronóstico', 'pronostico',          # es
    'previsioni', 'previsão', 'previsao', 'previsión', 'prevision',
    'tempo',                                                # it / pt
    'weer', 'weersverwachting',                             # nl
]

# Time words for the world clock (NOT tiempo/tempo, those are weather).
TIME = ['time', 'hora', 'horas', 'heure', 'heures', 'uhrzeit', 'uhr', 'zeit',
        'ora', 'ore', 'tijd', 'uur']

# Full "what time is it" questions, stripped before the place is read. These
# work even for languages whose phrasing carries no standalone time word
# (German "wie spät ist es", Dutch "hoe laat is het").
TIME_QUESTIONS = [
    'what time is it', "what's the time", 'what is the time', 'what time',  # en
    'quelle heure est-il', 'quelle heure est il',                          # fr
    'wie spät ist es', 'wie spaet ist es', 'wie viel uhr ist es', 'wieviel uhr ist es',  # de
    'qué hora es', 'que hora es',                                          # es
    'che ore sono', 'che ora è', 'che ora e',                             # it
    'que horas são', 'que horas sao',                                     # pt
    'hoe laat is het',                                                    # nl
]

# "my IP" phrases across languages.
MY_IP = [
    'my ip', "what's my ip", 'whats my ip', 'what is my ip', 'my ip address', 'ip address',
    'mon ip', 'mon adresse ip', 'quelle est mon ip', 'adresse ip',
    'meine ip', 'was ist meine ip', 'meine ip-adresse', 'ip-adresse', 'ip adresse',
    'mi ip', 'cuál es mi ip', 'cual es mi ip', 'mi dirección ip', 'mi direccion ip', 'dirección ip',
    'il mio ip', 'mio ip', 'qual è il mio ip', 'indirizzo ip',
    'meu ip', 'qual é o meu ip', 'qual e o meu ip', 'meu endereço ip', 'endereço ip',
    'mijn ip', 'wat is mijn ip', 'mijn ip-adres', 'ip-adres',
]

# Colour tool keyword phrases (exact match → default swatch).
COLOR_KEYWORDS = [
    'color picker', 'colour picker', 'color converter', 'colour converter',
    'hex to rgb', 'rgb to hex', 'hex to hsl', 'rgb to hsl', 'color', 'colour',
    'sélecteur de couleur', 'selecteur de couleur', 'couleur', 'convertisseur de couleur',
    'farbwähler', 'farbwaehler', 'farbe', 'farbwahl', 'farbauswahl',
    'selector de color', 'colore', 'selettore colore', 'selettore di colore',
    'seletor de cor', 'cor', 'kleurkiezer', 'kleur',
]
# Words that make a colour intent explicit (gate for bare hex / named colours).
COLOR_CONTEXT = ['hex', 'rgb', 'hsl', 'color', 'colour', 'couleur', 'farbe', 'colore', 'cor', 'kleur']

# Encode / decode verbs.
ENCODE_VERBS = ['encode', 'encoder', 'kodieren', 'codieren', 'codificar', 'codificare', 'coderen', 'encoderen']
DECODE_VERBS = ['decode', 'décoder', 'decoder', 'dekodieren', 'decodieren', 'decodificar', 'decodificare', 'decoderen']

# JSON tool keyword phrases.
JSON_KEYWORDS = [
    'json', 'json formatter', 'format json', 'json format', 'json validator',
    'validate json', 'json beautifier', 'prettify json', 'json pretty',
    'formatter json', 'formater json', 'valider json', 'json formatieren',
    'json validieren', 'formatear json', 'validar json', 'formattare json',
    'formatar json', 'json valideren', 'json opmaken',
]
JSON_VERBS = ['format', 'validate', 'prettify', 'beautify', 'formatter', 'formater',
              'valider', 'formatieren', 'validieren', 'formatear', 'validar',
              'formattare', 'formatar', 'valideren', 'opmaken']

# Regex tool keyword phrases.
REGEX_KEYWORDS = [
    'regex tester', 'regex test', 'test regex', 'regex', 'regexp', 'regular expression',
    'regex editor', 'regex checker', 'testeur regex', 'expression régulière',
    'expression reguliere', 'regulärer ausdruck', 'regulaerer ausdruck',
    'probador de regex', 'expresión regular', 'expresion regular',
    'espressione regolare', 'expressão regular', 'expressao regular',
    'reguliere expressie', 'reguliere uitdrukking',
]

# Words implying an HTTP status lookup (lets bare non-error codes through).
HTTP_WORDS = ['http', 'https', 'status', 'statut', 'estado', 'stato', 'statuscode',
              'código', 'codigo', 'codice', 'code', 'error', 'erreur', 'fehler',
              'errore', 'fout', 'erro']

# Port lookup nouns.
PORT_WORDS = ['port', 'puerto', 'porta', 'poort']

# "generate / create" lead-ins (random number, QR…).
GENERATE = QR_VERBS = [
    'generate', 'create', 'make', 'générer', 'generer', 'créer', 'creer',
    'erstellen', 'generieren', 'generar', 'crear', 'genera', 'crea',
    'generare', 'creare', 'gerar', 'criar', 'maken', 'genereren', 'aanmaken',
]
# Range "between" lead-in and separators ("1 to 10", "1 à 10", "entre 1 y 10").
RANGE_BETWEEN = ['between', 'entre', 'tussen', 'zwischen', 'tra', 'fra']
RANGE_SEP = ['-', 'to', 'and', ',', 'a', 'à', 'et', 'und', 'y', 'e', 'tot', 'bis', 'au']

# Random-number phrases / words.
RANDOM_WORDS = ['random number', 'random', 'rng', 'random num',
                'nombre aléatoire', 'nombre aleatoire', 'aléatoire', 'aleatoire',
                'zufallszahl', 'zufallsnummer', 'zufall',
                'número aleatorio', 'numero aleatorio', 'aleatorio',
                'numero casuale', 'casuale',
                'número aleatório', 'numero aleatório', 'aleatório',
                'willekeurig getal', 'willekeurig nummer', 'willekeurig']

# Dice verbs and nouns (NdM notation is universal).
DICE_VERBS = ['roll', 'lancer', 'würfeln', 'wuerfeln', 'tirar', 'lanzar', 'lancia',
              'lanciare', 'rolar', 'gooi', 'gooien', 'werfen']
DICE_NOUNS = ['dice', 'die', 'dé', 'dés', 'würfel', 'wuerfel', 'würfeln', 'wuerfeln',
              'dado', 'dados', 'dadi', 'dobbelsteen', 'dobbelstenen']

# Coin-flip phrases. Bare ambiguous single words (piece/moneda/munt…) are left
# out on purpose so ordinary searches aren't hijacked; the clear phrases remain.
COIN_KEYWORDS = [
    'flip a coin', 'coin flip', 'flip coin', 'coin toss', 'toss a coin',
    'heads or tails', 'flip', 'coin',
    'pile ou face', 'lancer une pièce', 'lancer une piece', 'jouer à pile ou face',
    'münzwurf', 'muenzwurf', 'münze werfen', 'muenze werfen', 'kopf oder zahl',
    'cara o cruz', 'lanzar una moneda', 'lanzar moneda', 'echar a cara o cruz',
    'testa o croce', 'lancia una moneta', 'lancia moneta', 'lanciare la moneta',
    'cara ou coroa', 'atirar moeda', 'lançar moeda', 'lancar moeda',
    'kop of munt', 'munt opgooien', 'munt of kop',
]

# Stopwatch vs timer vocab.
STOPWATCH_WORDS = ['stopwatch', 'chronomètre', 'chronometre', 'chrono', 'stoppuhr',
                   'cronómetro', 'cronometro', 'cronômetro']
TIMER_WORDS = ['timer', 'countdown', 'minuteur', 'compte à rebours', 'compte a rebours',
               'kurzzeitmesser', 'temporizador', 'cuenta atrás', 'cuenta atras',
               'conto alla rovescia', 'contagem regressiva', 'aftelklok', 'afteller']

# Duration unit words for timer parsing, grouped so "5 Stunden" ≠ "5 seconds".
DUR_HOURS = ['h', 'hr', 'hrs', 'hour', 'hours', 'heure', 'heures', 'stunde', 'stunden',
             'std', 'ora', 'ore', 'hora', 'horas', 'uur', 'uren']
DUR_MINS = ['m', 'min', 'mins', 'minute', 'minutes', 'minuto', 'minuti', 'minutos',
            'minuten', 'minuut', 'minuti', 'minuten']
DUR_SECS = ['s', 'sec', 'secs', 'second', 'seconds', 'seconde', 'secondes', 'sekunde',
            'sekunden', 'segundo', 'segundos', 'secondo', 'secondi', 'seconden']

# QR nouns (verbs reuse GENERATE above).
QR_NOUNS = ['qr code', 'qrcode', 'qr', 'code qr', 'código qr', 'codigo qr', 'codice qr', 'qr-code']

# Unix timestamp / epoch keyword phrases (bare "unix" excluded, too generic).
TIMESTAMP_KEYWORDS = [
    'unix timestamp', 'timestamp', 'epoch', 'epoch converter', 'unix time',
    'epoch time', 'timestamp converter', 'current timestamp', 'current epoch',
    'unix epoch', 'epoch unix', 'unixtime', 'now timestamp',
    'horodatage', 'timestamp unix', 'heure unix',                 # fr
    'zeitstempel', 'unix zeitstempel', 'unix-zeit',               # de
    'marca de tiempo', 'tiempo unix', 'sello de tiempo',          # es
    'marca temporale', 'tempo unix',                             # it
    'carimbo de tempo', 'tempo unix',                           # pt
    'tijdstempel', 'unix tijd',                                  # nl
]
# Words that name a human date on the right of "<epoch> to <date>".
DATE_WORDS = ['date', 'datetime', 'time', 'human', 'human date', 'date time',
              'fecha', 'data', 'datum', 'heure', 'uhrzeit', 'hora', 'human-readable']

# Password generator keyword phrases. A qualifier (generator/generate/random/
# strong/…) is required so a bare "password" search isn't hijacked.
PASSWORD_KEYWORDS = [
    'password generator', 'generate password', 'generate a password', 'random password',
    'strong password', 'create password', 'new password', 'pwgen', 'secure password',
    'générateur de mot de passe', 'mot de passe aléatoire', 'générer mot de passe',
    'générer un mot de passe',                                   # fr
    'passwort generator', 'passwort generieren', 'zufälliges passwort', 'sicheres passwort',
    'generador de contraseñas', 'contraseña aleatoria', 'generar contraseña', 'contraseña segura',
    'generatore di password', 'password casuale', 'genera password', 'password sicura',
    'gerador de senha', 'senha aleatória', 'gerar senha', 'senha segura',
    'wachtwoord generator', 'willekeurig wachtwoord', 'wachtwoord genereren', 'sterk wachtwoord',
]
