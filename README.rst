A neo4j/Django integration layer, based on `thobe`_'s original integration_ and `versae`_'s neo4jrestclient_.

.. _thobe: https://github.com/thobe/
.. _integration: http://journal.thobe.org/2009/12/seamless-neo4j-integration-in-django.html
.. _versae: https://github.com/versae/
.. _neo4jrestclient: https://github.com/versae/neo4j-rest-client/

Overview , or, What Does It Do?
===============================

neo4django is a Django ORM integration for neo4j. It allows model definitions that are very similar to Django's, and enables some other Django functionality (like signals).

The original Neo4j Django integration restricted database access to in-process. neo4django uses the Neo4j REST api to communicate with the database, freeing the database up for access by other processes and making it easy to host the database on another machine.

Other improvements over the original integration include

- A number of custom properties.
   - ``EmailProperty``
   - ``IntegerProperty``
   - ``DateTimeProperty``
   - ``URLProperty``
   - ``AutoProperty``
- Improved indexing support.
- Index-based querying.
- Fancier QuerySet usage.
- A significant test suite to empower future development.
- Developed to Django 1.3.
- Built to work alongside relational models.

What It Doesn't Do (TODO)
=========================

`thobe`_'s list of future features hasn't been impacted that much by our development. We're still working on

- Relationship models and querying
- Neo4j-specific Manager API (to enable traversal, etc).
- And, to a lesser extent, support for the Django admin interface.

Getting Started
===================

Using pip, you can install from PyPi::

    pip install neo4django

or straight from GitHub::

    pip install -e git+https://github.com/scholrly/neo4django/#egg=neo4django

Database Setup
==============

An example settings.py::

    NEO4J_DATABASES = {
        'default' : {
            'HOST':'localhost',
            'PORT':7474,
            'ENDPOINT':'/db/data'
        }
    }

Note that if you want to use Django auth or other packages built on the regular relational ORM, you'll still need a regular ``DATABASES`` setting and a supported database.

Models
==========

These look just like the Django models you're used to, but instead of inheriting from ``django.db.models.Model``, inherit from ``neo4django.db.models.NodeModel``, and use ``Property``s instead of typical fields::

    class Person(models.NodeModel):
        name = models.StringProperty()
        age = models.IntegerProperty()

Properties
==========

As you can see, some basic properties are provided::

    class OnlinePerson(Person):
        email = models.EmailProperty()
        homepage = models.URLProperty()

Some property types can also be indexed by neo4django. This will speed up subsequent queries based on those properties::

    class EmployedPerson(Person):
        job_title = models.StringProperty(indexed=True)

All instances of ``EmployedPerson`` will have their ``job_title`` properties indexed.

This might be a good time to mention a couple caveats.

1. Properties of value ``None`` are not currently indexed. I know, I'm sorry - working on it.
2. neo4django doesn't come with a migration tool! (Though if you're interested in writing one, talk to us!) If you flip a property to ``indexed=True``, make sure you update the graph manually, or re-index your models by resetting the property (per affected model instance) and saving.

Relationships
=============

Relationships are supported as in the original integration::

    class Pet(models.NodeModel):
        owner = models.Relationship(Person, 
                                    rel_type=neo4django.Incoming.OWNS,
                                    single=True,
                                    related_name='pets'
                                   )

... and like in relational Django, you can target a class that has yet to be defined with a string::

    class Pet(models.NodeModel):
        owner = models.Relationship('Person', 
                                    rel_type=neo4django.Incoming.OWNS,
                                    single=True,
                                    related_name='pets'
                                   )

And then in the interpreter::

    >>> pete = Person.objects.create(name='Pete', age=30)
    >>> garfield = Pet.objects.create()
    >>> pete.pets.add(garfield)
    >>> pete.save()
    >>> list(pete.pets.all())
    [<Pet: Pet object]

You can also add a new option, ``preserve_ordering``, to the ``Relationship``. In that case, the order of relationship creation will be persisted.

QuerySets
=========

QuerySets now implement more of the `Django QuerySet API`_, like ``get_or_create``, ``in_bulk``, and ``select_related``.

They accept a slew of useful field lookups- namely

- exact
- gt
- lt
- gte
- lte
- range
- in
- contains
- and startswith

We've also added a new field lookup - "member" - to allow exact queries against elements inside an array.

More will be implemented soon - they're pretty easy, and a great place to contribute!

QuerySets take advantage of indexed properties, typing, and REST paged traversals to get you what you want, faster.

.. _Django QuerySet API: https://docs.djangoproject.com/en/1.3/ref/models/querysets/

Working Alongside Django ORM
============================

If you'd like to use Django with Neo4j and a relational database, we've got you covered. Simply install the provided database router, and the two ORMs will play nice.

In you settings.py::

    DATABASE_ROUTERS = ['neo4django.utils.Neo4djangoIntegrationRouter']

Performance
===========

neo4django comes with simple benchmarks that we are using to actively improve performance. Currently, query performance is fairly respectable, while creation performance is poor. In upcoming releases, performance will be improved by taking further advantage of the REST client's batch support and Cypher and Gremlin plugins.

To make querying even more performant, we've implemented `select_related`_. The implementation works just like Django's, without the restrictions on relationship types, and with the additional default of ``depth=1``- this is a graph database, after all, and an infinite select_related could very well include the whole graph!

To use ``select_related``, call it on a ``NodeQuerySet`` with either a max depth or a brand of field lookups described in the docs_::

    Person.objects.all(name='Jack').select_related(depth=5)
    #OR
    Person.objects.get(name='Jack').select_related('spouse__mother__sister__son__stepdad')

...either of which will pre-load Jack's extended family so he can go about recalling names :)

.. _select_related: https://docs.djangoproject.com/en/dev/ref/models/querysets/#select-related
.. _docs: https://docs.djangoproject.com/en/dev/ref/models/querysets/#select-related
Concurrency
===========

Because of the difficulty of transactionality over the REST API, using neo4django from multiple threads, or connecting to the same Neo4j instance from multiple servers, is not recommended. That said, we do, in fact, do this in testing environments. Hotspots like type hierarchy management are transactional, so as long as you can separate the entities being manipulated in the graph, concurrent use of neo4django is possible.


Multiple Databases
==================

We wrote neo4django to support multiple databases- but haven't tested it. In the future, we'd like to fully support multiple databases and routing similar to that already in Django.

Further Introspection
=====================

When possible, neo4django follows Django ORM, and thus allows some introspection of the schema. Because Neo4j is schema-less, though, further introspection and a more dynamic data layer can be handy. Initially, there's only one additional option to enable decoration of ``Property`` s and ``Relationship`` s - ``metadata`` ::

    class N(models.NodeModel):
        name = models.StringProperty(metadata={'authoritative':True})
        aliases = models.StringArrayProperty(metadata={'authoritative':False, 'authority':name})

    >>> for field in N._meta.fields:
    ...     if hasattr(field, 'meta'):
    ...         if field.meta['authoritative']:
    ...             #do something with the authoritative field

Running the Test Suite
======================

The test suite requires that Neo4j be running on localhost:7474, and that you have the cleandb_ extension installed.

We test with nose_. To run the suite, set ``test_settings.py`` as your ``DJANGO_SETTINGS_MODULE`` and run ``nosetests``. In bash, that's simply::

    cd <your path/neo4django/
    export DJANGO_SETTINGS_MODULE="neo4django.tests.test_settings"
    nosetests

We've put together a nose plugin_ to ensure that regression tests pass. Any changesets that fail regression tests will be denied a pull. To run the tests, simply::

    pip install nose-regression
    nosetests --with-regression

.. _cleandb: https://github.com/jexp/neo4j-clean-remote-db-addon
.. _nose: http://readthedocs.org/docs/nose/en/latest/
.. _plugin: https://github.com/scholrly/nose-regression

Contributing
============

All contributions, no matter how small, are greatly appreciated!

If you do decide to contribute, please test! If a pull request fails any regression tests, we won't be able to accept it.

