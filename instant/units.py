"""Unit definitions and conversion logic.

A single source of truth for unit conversion: the Python side parses queries
and computes the initial answer, and ``category_payload`` serialises the same
factors to the browser so the converter widget can recompute live without a
round trip.

Each non-temperature unit stores a ``factor`` = how many base units it equals
(base unit has factor 1.0). Converting is therefore::

    result = value * factor[from] / factor[to]

Temperature is affine, so it is handled with explicit formulas.
"""

from django.utils.translation import gettext_lazy as _

# Translatable display labels per category. Kept separate from ``UNIT_DEFS``
# (whose ``label`` stays a plain English key) so these literals are picked up
# by ``makemessages`` and resolved in the active UI language at render time.
CATEGORY_LABELS = {
    'length': _('Length'),
    'weight': _('Weight'),
    'temperature': _('Temperature'),
    'volume': _('Volume'),
    'area': _('Area'),
    'speed': _('Speed'),
    'data': _('Data'),
    'time': _('Time'),
}

# category -> {label, base, units: {key: (factor, symbol, [aliases...])}}
UNIT_DEFS = {
    'length': {
        'label': 'Length',
        'base': 'm',
        'units': {
            'mm': (0.001, 'mm', ['mm', 'millimeter', 'millimeters', 'millimetre', 'millimetres',
                                 'millimètres', 'milímetros', 'millimetri']),
            'cm': (0.01, 'cm', ['cm', 'centimeter', 'centimeters', 'centimetre', 'centimetres',
                                'centimètres', 'centímetros', 'centimetri']),
            'm': (1.0, 'm', ['m', 'meter', 'meters', 'metre', 'metres',
                             'mètre', 'mètres', 'metro', 'metros', 'metri']),
            'km': (1000.0, 'km', ['km', 'kilometer', 'kilometers', 'kilometre', 'kilometres',
                                  'kilomètres', 'kilómetros', 'chilometri', 'quilômetros', 'quilometros']),
            'in': (0.0254, 'in', ['in', 'inch', 'inches', '"', 'pouce', 'pouces', 'pulgada',
                                  'pulgadas', 'pollici', 'polegadas', 'duim']),
            'ft': (0.3048, 'ft', ['ft', 'foot', 'feet', "'", 'pied', 'pieds', 'pies', 'piedi',
                                  'pés', 'pes', 'voet', 'voeten']),
            'yd': (0.9144, 'yd', ['yd', 'yard', 'yards']),
            'mi': (1609.344, 'mi', ['mi', 'mile', 'miles', 'milles', 'millas', 'miglia', 'milhas']),
            'nmi': (1852.0, 'nmi', ['nmi', 'nauticalmile', 'nauticalmiles']),
        },
    },
    'mass': {
        'label': 'Weight',
        'base': 'g',
        'units': {
            'mg': (0.001, 'mg', ['mg', 'milligram', 'milligrams']),
            'g': (1.0, 'g', ['g', 'gram', 'grams', 'gramme', 'grammes', 'gramo', 'gramos',
                             'grammo', 'grammi', 'grama', 'gramas']),
            'kg': (1000.0, 'kg', ['kg', 'kilogram', 'kilograms', 'kilo', 'kilos', 'kilogramme',
                                  'kilogrammes', 'kilogramo', 'kilogramos', 'chilogrammi',
                                  'quilograma', 'quilogramas']),
            't': (1_000_000.0, 't', ['t', 'tonne', 'tonnes', 'metricton', 'metrictons']),
            'oz': (28.349523125, 'oz', ['oz', 'ounce', 'ounces', 'once', 'onces', 'onza',
                                        'onzas', 'oncia', 'onças', 'ons']),
            'lb': (453.59237, 'lb', ['lb', 'lbs', 'pound', 'pounds', 'livre', 'livres', 'libra',
                                     'libras', 'libbra', 'libbre', 'pfund', 'pond', 'ponden']),
            'st': (6350.29318, 'st', ['st', 'stone', 'stones']),
        },
    },
    'temperature': {
        'label': 'Temperature',
        'base': 'c',
        'units': {
            'c': (1.0, '°C', ['c', '°c', 'celsius', 'centigrade']),
            'f': (1.0, '°F', ['f', '°f', 'fahrenheit']),
            'k': (1.0, 'K', ['k', 'kelvin']),
        },
    },
    'volume': {
        'label': 'Volume',
        'base': 'l',
        'units': {
            'ml': (0.001, 'mL', ['ml', 'milliliter', 'milliliters', 'millilitre', 'millilitres',
                                 'mililitros', 'millilitri']),
            'l': (1.0, 'L', ['l', 'liter', 'liters', 'litre', 'litres', 'litro', 'litros', 'litri']),
            'm3': (1000.0, 'm³', ['m3', 'cubicmeter', 'cubicmeters', 'cubicmetre', 'cubicmetres']),
            'tsp': (0.00492892159, 'tsp', ['tsp', 'teaspoon', 'teaspoons']),
            'tbsp': (0.01478676478, 'tbsp', ['tbsp', 'tablespoon', 'tablespoons']),
            'floz': (0.0295735295625, 'fl oz', ['floz', 'fluidounce', 'fluidounces']),
            'cup': (0.2365882365, 'cup', ['cup', 'cups']),
            'pt': (0.473176473, 'pt', ['pt', 'pint', 'pints']),
            'qt': (0.946352946, 'qt', ['qt', 'quart', 'quarts']),
            'gal': (3.785411784, 'gal', ['gal', 'gallon', 'gallons']),
        },
    },
    'area': {
        'label': 'Area',
        'base': 'm2',
        'units': {
            'cm2': (0.0001, 'cm²', ['cm2', 'squarecentimeter', 'squarecentimeters']),
            'm2': (1.0, 'm²', ['m2', 'squaremeter', 'squaremeters', 'squaremetre', 'squaremetres']),
            'km2': (1_000_000.0, 'km²', ['km2', 'squarekilometer', 'squarekilometers']),
            'ha': (10_000.0, 'ha', ['ha', 'hectare', 'hectares']),
            'sqin': (0.00064516, 'in²', ['sqin', 'squareinch', 'squareinches']),
            'sqft': (0.09290304, 'ft²', ['sqft', 'squarefoot', 'squarefeet']),
            'sqyd': (0.83612736, 'yd²', ['sqyd', 'squareyard', 'squareyards']),
            'acre': (4046.8564224, 'ac', ['acre', 'acres']),
            'sqmi': (2_589_988.110336, 'mi²', ['sqmi', 'squaremile', 'squaremiles']),
        },
    },
    'speed': {
        'label': 'Speed',
        'base': 'mps',
        'units': {
            'mps': (1.0, 'm/s', ['mps', 'm/s', 'meterspersecond', 'metrespersecond']),
            'kmh': (0.277777778, 'km/h', ['kmh', 'km/h', 'kph', 'kilometersperhour', 'kilometresperhour']),
            'mph': (0.44704, 'mph', ['mph', 'milesperhour']),
            'fps': (0.3048, 'ft/s', ['fps', 'ft/s', 'feetpersecond']),
            'knot': (0.514444444, 'kn', ['knot', 'knots', 'kn']),
        },
    },
    'data': {
        'label': 'Data',
        'base': 'B',
        'units': {
            'bit': (0.125, 'bit', ['bit', 'bits', 'b']),
            'B': (1.0, 'B', ['byte', 'bytes']),
            'KB': (1000.0, 'KB', ['kb', 'kilobyte', 'kilobytes']),
            'MB': (1_000_000.0, 'MB', ['mb', 'megabyte', 'megabytes']),
            'GB': (1_000_000_000.0, 'GB', ['gb', 'gigabyte', 'gigabytes']),
            'TB': (1_000_000_000_000.0, 'TB', ['tb', 'terabyte', 'terabytes']),
            'KiB': (1024.0, 'KiB', ['kib', 'kibibyte', 'kibibytes']),
            'MiB': (1_048_576.0, 'MiB', ['mib', 'mebibyte', 'mebibytes']),
            'GiB': (1_073_741_824.0, 'GiB', ['gib', 'gibibyte', 'gibibytes']),
            'TiB': (1_099_511_627_776.0, 'TiB', ['tib', 'tebibyte', 'tebibytes']),
        },
    },
    'time': {
        'label': 'Time',
        'base': 's',
        'units': {
            'ms': (0.001, 'ms', ['ms', 'millisecond', 'milliseconds']),
            's': (1.0, 's', ['s', 'sec', 'secs', 'second', 'seconds']),
            'min': (60.0, 'min', ['min', 'mins', 'minute', 'minutes']),
            'h': (3600.0, 'h', ['h', 'hr', 'hrs', 'hour', 'hours']),
            'day': (86400.0, 'day', ['day', 'days']),
            'week': (604800.0, 'wk', ['week', 'weeks', 'wk']),
            'month': (2_629_800.0, 'mo', ['month', 'months']),
            'year': (31_557_600.0, 'yr', ['year', 'years', 'yr', 'yrs']),
        },
    },
}


def _build_alias_index():
    index = {}
    for category, cdef in UNIT_DEFS.items():
        for key, (_factor, _symbol, aliases) in cdef['units'].items():
            for alias in aliases:
                # First registration wins; keeps 'm' = length-meter over later
                # collisions. Aliases are curated to avoid genuine clashes.
                index.setdefault(alias, (category, key))
    return index


ALIAS_INDEX = _build_alias_index()


def lookup_unit(token):
    """Return (category, unit_key) for a unit token, or None."""
    if not token:
        return None
    token = token.strip().lower().replace('²', '2').replace('³', '3')
    return ALIAS_INDEX.get(token)


def convert(category, value, from_key, to_key):
    """Convert ``value`` from one unit to another within a category."""
    if category == 'temperature':
        return _convert_temperature(value, from_key, to_key)
    units = UNIT_DEFS[category]['units']
    base_value = value * units[from_key][0]
    return base_value / units[to_key][0]


def _convert_temperature(value, from_key, to_key):
    # Normalise to Celsius, then to target.
    if from_key == 'c':
        c = value
    elif from_key == 'f':
        c = (value - 32) * 5 / 9
    else:  # kelvin
        c = value - 273.15
    if to_key == 'c':
        return c
    if to_key == 'f':
        return c * 9 / 5 + 32
    return c + 273.15  # kelvin


def symbol(category, key):
    return UNIT_DEFS[category]['units'][key][1]


def category_payload(category):
    """JSON-serialisable unit data for the in-browser converter widget."""
    cdef = UNIT_DEFS[category]
    return {
        'category': category,
        'label': str(CATEGORY_LABELS.get(category, cdef['label'])),
        'temperature': category == 'temperature',
        'units': [
            {'key': key, 'symbol': sym, 'factor': factor}
            for key, (factor, sym, _aliases) in cdef['units'].items()
        ],
    }
