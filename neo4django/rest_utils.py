from operator import itemgetter, add
from itertools import izip_longest, chain, ifilter

def id_from_url(url):
    from urlparse import urlsplit
    from posixpath import dirname, basename
    path = urlsplit(url).path
    b = basename(path)
    return int(b if b else dirname(path))

#if only there were a real itemdropper
def itemdropper(*ind):
    """
    A complement to itemgetter. Accepts ints representing indexes. Returns a
    function that will exclude all elements with those indexes from a provided
    seq.

    Note that, unlike itemgetter, itemdropper doesn't support slices or non-int
    keys, due to complexity.
    """
    def func(seq):
        return reduce(add, (seq[i:i+1] for i in xrange(len(seq)) if i not in ind))
    return func

class Neo4jTable(object):
    def __init__(self, d):
        self.data = d['data']
        self.column_names = d['columns']

    def get_column_indexes(self, column_name_pred):
        if column_name_pred is None:
            column_name_pred = lambda s:s
        elif not callable(column_name_pred):
            if isinstance(column_name_pred, basestring):
                column_name = column_name_pred
                column_name_pred = lambda s: s == column_name
            else:
                column_names = column_name_pred
                column_name_pred = lambda s: s in column_names
        return [i for i,c in 
                enumerate(self.column_names) if column_name_pred(c)]

    def get_all_rows(self, column_name_pred):
        """
        Return all table columns that match the provided predicate.

        If the argument is a string, test for equality instead- if it's another
        seq, test for membership. If the argument is None, the identity function
        is assumed.
        """
        columns = self.get_column_indexes(column_name_pred)
        col_getter = itemgetter(*columns)
        return chain.from_iterable(rc if isinstance(rc, tuple) else (rc,)
                                 for rc in (col_getter(r)
                                            for r in self.data))

    def drop_columns(self, column_name_pred):
        columns = self.get_column_indexes(column_name_pred)
        col_dropper = itemdropper(*columns)
        self.column_names = col_dropper(self.column_names)
        self.data = [col_dropper(r) for r in self.data]

    def append_column(self, column_name, column_rows):
        if len(column_rows) != len(self):
            raise ValueError('New columns are expected to have the same length'
                             ' as existing columns.')
        self.column_names = self.column_names + [column_name]
        self.data = [ list(r) + [new_element] for r, new_element in
                     izip_longest(self.data, column_rows)]

    def to_dicts(self):
        def to_dict(row):
            return dict(izip_longest(self.column_names, row))
        return [to_dict(r) for r in self.data]

    def __len__(self):
        return len(self.data)

def prettify_path(path_dict):
    nodes = ['(%d)' % id_from_url(url) for url in path_dict['nodes']]
    rels = ['[%d]' % id_from_url(url) for url in path_dict['relationships']]
    return '->'.join(ifilter(None, chain.from_iterable(izip_longest(nodes, rels))))
