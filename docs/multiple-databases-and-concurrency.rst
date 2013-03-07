================================
Multiple Databases & Concurrency
================================

Multiple Databases
==================

neo4django was written to support multiple databases- but that support is
untested. In the future, we'd like to fully support multiple databases and
routing similar to that already in Django. Because most of the infrastucture
is complete, robust support would be a great place to 
`contribute <https://github.com/scholrly/neo4django>`_.

Concurrency
===========

Because of the difficulty of transactionality over the REST API, using
neo4django from multiple threads, or connecting to the same Neo4j instance from
multiple servers, is not recommended without serious testing.

That said, a number of users do this in production. Hotspots like type hierarchy
management are transactional, so as long as you can separate the entities being
manipulated in the graph, concurrent use of neo4django is possible.
