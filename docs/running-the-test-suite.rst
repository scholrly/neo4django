======================
Running the Test Suite
======================

The test suite requires that Neo4j be running, and that you have the cleandb_
extension installed at ``localhost:<NEO4J_PORT>/cleandb``.

We test with nose_. To run the suite, set ``test_settings.py`` as your
``DJANGO_SETTINGS_MODULE`` and run ``nosetests``. In bash, that's
simply::

    cd <your path>/neo4django/
    export DJANGO_SETTINGS_MODULE="neo4django.tests.test_settings"
    nosetests

We've put together a nose plugin_ to ensure that regression tests pass. Any
changesets that fail regression tests will be denied a pull. To run the tests,
simply::

    pip install nose-regression
    nosetests --with-regression

.. _cleandb: https://github.com/jexp/neo4j-clean-remote-db-addon
.. _nose: http://readthedocs.org/docs/nose/en/latest/
.. _plugin: https://github.com/scholrly/nose-regression
