====================
Writing Django Tests
====================

There is a custom test case included which you can use to write Django tests
that need access to :class:`~neo4django.db.models.NodeModel` instances. If 
properly configured, it will wipe out the Neo4j database in between each test.
To configure it, you must set up a Neo4j instance with the cleandb_ extension
installed. If your neo4j instance were configured at port 7475, and your
cleandb install were pointing to ``/cleandb/secret-key``, then you would put
the following into your ``settings.py``::

    NEO4J_TEST_DATABASES = {
        'default': {
            'HOST': 'localhost',
            'PORT': 7475,
            'ENDPOINT': '/db/data',
            'OPTIONS': {
                'CLEANDB_URI': '/cleandb/secret-key',
                'username': 'lorem',
                'password': 'ipsum',
            }
        }
    }

With that set up, you can start writing test cases that inherit from
:class:`neo4django.testcases.NodeModelTestCase` and run them as you normally would
through your Django test suite.

.. _cleandb: https://github.com/jexp/neo4j-clean-remote-db-addon
