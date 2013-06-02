.. neo4django documentation master file, created by
   sphinx-quickstart on Thu Mar  7 12:27:41 2013.
   You can adapt this file completely to your liking, but it should at least
   contain the root `toctree` directive.

===============================
neo4django - User Documentation
===============================

**neo4django** is an Object Graph Mapper that let's you use familiar Django model definitions and queries against the Neo4j graph database.

You can install the latest stable release from PyPi

.. code-block:: console

    > pip install neo4django

or get the bleeding-edge from GitHub.

.. code-block:: console

    > pip install -e git+https://github.com/scholrly/neo4django/#egg=neo4django-dev

Details
=======

:doc:`getting-started`

Configure your project to connect to Neo4j.

:doc:`writing-models`

Define models to interact with the database.

:doc:`querying`

Query against models in Neo4j.

:doc:`auth`

Store and interact with users in Neo4j.

:doc:`admin`

Use Django's admin interface with Neo4j.

:doc:`writing-django-tests`

:doc:`migrations`

:doc:`debugging-and-optimization`

:doc:`multiple-databases-and-concurrency`

:doc:`running-the-test-suite`

.. toctree::
   :hidden:

   getting-started
   writing-models
   querying
   auth
   writing-django-tests
   debugging-and-optimization
   multiple-databases-and-concurrency
   running-the-test-suite

Contributing
============

We love contributions, large or small. The source is available on `GitHub <https://github.com/scholrly/neo4django>`_- fork the project and submit a pull request with your changes.

Uncomfortable / unwilling to code? If you'd like, you can give a small donation on `Gittip <https://www.gittip.com/mhluongo/>`_ to support the project.


Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`

