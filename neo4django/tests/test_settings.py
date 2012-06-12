NEO4J_DATABASES = {
    'default' : {
        'HOST':'localhost',
        'PORT':7474,
        'ENDPOINT':'/db/data'
    },
    'custom': {
        'HOST':'localhost',
        'PORT':7474,
        'ENDPOINT':'/db/data',
        'CLIENT': 'neo4django.tests.neo4jclient_tests.MyGraphDatabase'
    },
}

DEBUG = True

NEO4DJANGO_PROFILE_REQUESTS = False
NEO4DJANGO_DEBUG_GREMLIN = False

NEO4J_DELETE_KEY = 'supersecretdebugkey!'