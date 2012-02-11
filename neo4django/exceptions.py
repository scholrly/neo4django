class Error(Exception):
    """Base class for neo4django exceptions."""
    pass

class GremlinLibraryCouldNotBeLoaded(Error):
    """
    Error for when the Gremlin library could not be loaded within the number of
    acceptable retries. This could happen if the database's GremlinScriptEngine
    is being reset more than usual.
    """
    pass

class NoSuchDatabaseError(Error):
    def __init__(self, url=None, name=None):
        """
        Error for when a neo4j node without a configured database is provided,
        or a database name that doesn't exist in settings is provided.
        """
        if url is None and name is None:
            raise ValueError('A url or name identifying the problem database '
                             'must be provided.')
        self.url = url
        self.name = name

    def __str__(self):
        return 'No such database exists: %s'.format(str(self.url or self.name))
