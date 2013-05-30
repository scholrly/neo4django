======================
Running the Test Suite
======================


----------
virtualenv
----------

It is recommended that you develop and run tests from within the confines of a
virtualenv. If you have virtualenv installed, create the new environment by
executing::

    $> virtualenv neo4django

Once created, clone a local copy of the neo4django source::

    $> cd neo4django
    $> git clone https://github.com/scholrly/neo4django src/neo4django

After you have a virtualenv created, you must activate it::

    $> source <venv_path>/bin/activate


-------------------
Neo4j Test Instance
-------------------

The test suite requires that Neo4j be running, and that you have the cleandb_
extension installed at ``localhost:7474/cleandb``. You must download the
appropriate cleandb_ version that matches the version of Neo4j you have running.
Place the plugin jar in ``<NEO4J_PATH>/plugins`` and edit ``<NEO4J_PATH>/conf/neo4j-server.properties``
to include the following::

    org.neo4j.server.thirdparty_jaxrs_classes=org.neo4j.server.extension.test.delete=/cleandb
    org.neo4j.server.thirdparty.delete.key=supersecretdebugkey!

The first line represents the URL endpoint for invoking cleandb, and the second line
is the password to use the cleandb extension. You can change these values to whatever
makes most sense to you, but keep in mind that the test suite currently expects
``/cleandb`` and ``supersecretdebugkey!`` for both the URL and password respectively.
If you choose to use different values, you will need to edit ``neo4django/tests/test_settings.py``
to reflect your local changes.

If you are testing on a linux platform, you may also easily spin up a local test
Neo4j instance by using the packaged ``install_local_neo4j.bash`` script. This script
will retrieve a specified version of the community package of Neo4j and install it
into a ``lib`` folder in your current working directory. The script will also retrieve
and install the cleandb_ extension and install it as well.

By default, running ``install_local_neo4j.bash`` with no arguments will install version
1.8.2, as this is the oldest version run for Travis CI builds and supported by neo4django.
If you would like to test another version, ``install_local_neo4j.bash`` accepts a version
number as an argument. Currently, Travis CI builds are run against 1.8.2 and 1.9.RC1
versions of Neo4j; tests against 1.7.2 are run, but expected to fail. Once installed,
start the local Neo4j instance via ``lib/neo4j-community-<VERSION>/bin/neo4j start``.
Similarly, you can stop the local instance via ``lib/neo4j-community-<VERSION>bin/neo4j stop``.


-------------
Running Tests
-------------

If you are working withing an virtualenv (and you should be), activate your venv
(see above) and use ``pip`` to install both the core requirements and the requirements
for running tests::

    $> pip install -r requirements.txt -r test_requirements.txt

Since testing involves working with django, you will need to export an environment
variable for the included test django settings::

    $> export DJANGO_SETTINGS_MODULE=neo4django.tests.test_settings

Now you can run the test suite. All tests in the neo4django test suite are expected
to be run with nose_ and use a plugin_ for ensuring that regression tests pass (both
are installed for you if you pip install the test requirements). To run the test suite,
simply issue the following::

    $> cd <path_to>/neo4django
    $> nosetests --with-regression

This may give you some output about failing tests, but you should be most interesting in
the final output in which a report is given about tests passing or failing regression
tests. Note, that ANY changeset that fails regression tests will be denied a pull.


.. _cleandb: https://github.com/jexp/neo4j-clean-remote-db-addon
.. _nose: http://readthedocs.org/docs/nose/en/latest/
.. _plugin: https://github.com/scholrly/nose-regression
