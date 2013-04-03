import os
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

AUTHENTICATION_BACKENDS = ('neo4django.auth.backends.NodeModelBackend',)

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
        'OPTIONS':{
            'CLEANDB_URI': '/cleandb/supersecretdebugkey!',
        },
    },
}

NEO4J_TEST_DATABASES = {
    'default' : {
        'HOST':'localhost',
        'PORT':7474,
        'ENDPOINT':'/db/data',
        'OPTIONS':{
            'CLEANDB_URI': '/cleandb/supersecretdebugkey!',
        }
    }
}

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': os.path.join(BASE_DIR, 'db', 'test_database.sqlite3')
    }
}

DATABASE_ROUTERS = ['neo4django.utils.Neo4djangoIntegrationRouter']

USE_TZ = True

INSTALLED_APPS = (
    'neo4django.tests',   
)

SECRET_KEY="shutupdjangowe'retryingtotesthere"

DEBUG = True

NEO4DJANGO_PROFILE_REQUESTS = False
NEO4DJANGO_DEBUG_GREMLIN = False
