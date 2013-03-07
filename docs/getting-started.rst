===============
Getting Started
===============

Once you've installed neo4django, you can configure your Django project to connect to Neo4j.

Database Setup
==============

An example settings.py::

    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': os.path.join(BASE_DIR, 'db', 'test_database.sqlite3')
        }
    }

    NEO4J_DATABASES = {
        'default' : {
            'HOST':'localhost',
            'PORT':7474,
            'ENDPOINT':'/db/data'
        }
    }

If you'd like to use other Django apps built on the regular ORM in conjunction with neo4django, you'll still need to configure ``DATABASES`` with a supported database. You should also install a database router in your settings.py so the databases will play nice::

    DATABASE_ROUTERS = ['neo4django.utils.Neo4djangoIntegrationRouter']

Once your project is configured, you're ready to start :ref:`writing-models` !
