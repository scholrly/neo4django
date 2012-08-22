NEO4J_DATABASES = {
    'default' : {
        'HOST':'localhost',
        'PORT':7474,
        'ENDPOINT':'/db/data',
        'OPTIONS':{
            'CLEANDB_URI': '/cleandb/supersecretdebugkey!',
        },
    },
    'custom': {
        'HOST':'localhost',
        'PORT':7474,
        'ENDPOINT':'/db/data',
        'CLIENT': 'neo4django.tests.neo4jclient_tests.MyGraphDatabase',
    },
}

INSTALLED_APPS = (
    'neo4django.tests',   
)

DEBUG = True

NEO4DJANGO_PROFILE_REQUESTS = False
NEO4DJANGO_DEBUG_GREMLIN = False
