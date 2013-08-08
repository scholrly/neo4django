from django.db import models as dj_models
from django.db.models import signals
from django.conf import settings

import neo4jrestclient.client as neo_client
import neo4jrestclient.constants as neo_constants

from neo4django.db import connections, DEFAULT_DB_ALIAS
from neo4django.exceptions import NoSuchDatabaseError
from neo4django.decorators import (not_implemented,
                                   alters_data,
                                   transactional,
                                   not_supported,
                                   memoized)

from .manager import NodeModelManager

import inspect
import itertools
import re
from decorator import decorator


class IdProperty(object):
    def __init__(self, getter, setter):
        self.getter = getter
        self.setter = setter

    def __get__(self, inst, cls):
        if inst is None:
            return IdLookup(cls)
        else:
            return self.getter(inst)

    def __set__(self, inst, value):
        return self.setter(inst, value)


class IdLookup(object):
    indexed = True
    unique = True
    id = True
    name = 'id'
    attname = name

    def __init__(self, model):
        self.__model = model
    index = property(lambda self: self)

    def to_neo(self, value):
        if value is not None:
            return int(value)
        else:  # Allows lookups on Nulls
            return value

    def to_python(self, value):
        return self.to_neo(value)


class NeoModelBase(type(dj_models.Model)):
    """
    Model metaclass that adds creation counters to models, a hook for adding
    custom "class Meta" style options to NeoModels beyond those supported by
    Django, and method transactionality.
    """
    meta_additions = ['has_own_index']

    def __init__(cls, name, bases, dct):
        super(NeoModelBase, cls).__init__(name, bases, dct)
        cls._creation_counter = 0

    def __new__(cls, name, bases, attrs):
        super_new = super(NeoModelBase, cls).__new__
        #process the extra meta options
        attr_meta = attrs.get('Meta', None)
        extra_options = {}
        if attr_meta:
            for key in set(NeoModelBase.meta_additions + cls.meta_additions):
                if hasattr(attr_meta, key):
                    extra_options[key] = getattr(attr_meta, key)
                    delattr(attr_meta, key)
        #find all methods flagged transactional and decorate them
        flagged_methods = [i for i in attrs.items()
                           if getattr(i[1], 'transactional', False) and
                           inspect.isfunction(i[1])]

        @decorator
        def trans_method(func, *args, **kw):
            #the first arg should be 'self', since these functions are to be
            #converted to methods. if there's another transaction in progress,
            #do nothing
            #TODO prevents nested transactions, reconsider
            if len(args) > 0 and isinstance(args[0], NodeModel) and\
               len(connections[args[0].using]._transactions) < 1:
                #tx = connections[args[0].using].transaction()
                #TODO this is where generalized transaction support will go,
                #when it's ready in neo4jrestclient
                ret = func(*args, **kw)
                #tx.commit()
                return ret
            else:
                return func(*args, **kw)
        for i in flagged_methods:
            attrs[i[0]] = trans_method(i[1])
        #call the superclass method
        new_cls = super_new(cls, name, bases, attrs)
        # fix the pk field, which will be improperly set for concretely
        # inherited classes
        if not new_cls._meta.abstract:
            new_cls._meta.pk = new_cls.id
        #set the extra meta options
        for k in extra_options:
            setattr(new_cls._meta, k, extra_options[k])
        return new_cls


class NeoModel(dj_models.Model):
    __metaclass__ = NeoModelBase

    class Meta:
        abstract = True

    class creation_counter(object):
        def __get__(self, obj, cls):
            if getattr(cls, '_creation_counter', None) is None:
                cls._creation_counter = 1
            else:
                cls._creation_counter += 1
            return cls._creation_counter
    creation_counter = creation_counter()


class NodeModel(NeoModel):
    objects = NodeModelManager()
    _indexes = {}

    class Meta:
        abstract = True

    def __init__(self, *args, **kwargs):
        self.__using = kwargs.pop('using', DEFAULT_DB_ALIAS)
        super(NodeModel, self).__init__(*args, **kwargs)

    @classmethod
    def _neo4j_instance(cls, neo_node):
        #A factory method to create NodeModels from a neo4j node.
        instance = cls.__new__(cls)
        instance.__node = neo_node
        
        #take care of using by inferring from the neo4j node
        names = []
        for name in connections:
            connection_url = connections[name].url
            # Remove the authentication part
            connection_url = re.sub("http(s?)\:\/\/\w+:\w+\@", "", connection_url, flags=re.I)

            if connection_url in neo_node.url:
                names.append(name)

        if len(names) < 1:
            raise NoSuchDatabaseError(url=neo_node.url)

        instance.__using = names[0]

        #TODO: this violates DRY (BoundProperty._all_properties_for...)
        def get_props(cls):
            meta = cls._meta
            if hasattr(meta, '_properties'):
                properties = meta._properties
            else:
                meta._properties = properties = {}
            return properties

        all_properties = {}
        all_properties.update(get_props(cls))

        for parent in cls.mro():
            if hasattr(parent, '_meta'):
                all_properties.update(get_props(parent))

        #XXX assumes in-db name is the model attribute name, which will change after #30
        for key in all_properties:
            val = None
            if key in neo_node.properties:
                val = all_properties[key].to_python(neo_node.properties[key])
            else:
                val = all_properties[key].get_default()
            setattr(instance, key, val)

        return instance

    def _get_pk_val(self, meta=None):
        return self.__node.id if self.__node else None

    def _set_pk_val(self, value):
        if self.__node is None:
            if value is not None:
                self.__node = self.connection.nodes[value]
        else:
            raise TypeError("Cannot change the id of nodes.")

    pk = id = IdProperty(_get_pk_val, _set_pk_val)

    def __eq__(self, other):
        if type(self) != type(other):
            return False
        pk1 = self._get_pk_val()
        pk2 = other._get_pk_val()
        if pk1 is not None and pk2 is not None:
            return self._get_pk_val() == other._get_pk_val()
        elif pk1 is None and pk2 is None:
            return id(self) == id(other)
        return False

    @property
    def using(self):
        return self.__using

    @classmethod
    def from_model(cls, neo_model):
        """
        Factory method that essentially allows "casting" from a saved model
        instance to another saved model instance. These instances are both
        represented by the same node in the graph, but allow different views
        of the same properties and relationships.
        """
        if neo_model.pk:
            new_model = cls._neo4j_instance(neo_model.node)
            return new_model
        else:
            return cls.copy_model(neo_model)

    @classmethod
    def copy_model(cls, neo_model):
        onto_field_names = [f.attname for f in neo_model._meta.fields]
        new_model = cls()
        for field in neo_model._meta.fields:
            name = field.attname
            if name not in onto_field_names or name in ('pk', 'id'):
                continue
            val = getattr(neo_model, name)
            if isinstance(val, dj_models.Manager):
                for obj in val.all():
                    getattr(new_model, name).add(obj)
            else:
                setattr(new_model, name, val)
        return new_model

    @classmethod
    def index(cls, using=DEFAULT_DB_ALIAS):
        if cls in cls._indexes and using in cls._indexes[cls]:
            return cls._indexes[cls][using]

        index_name = cls.index_name(using)
        conn = connections[using]

        def get_index(name):
            #XXX this is a hack bc of bad equality tests for indexes in
            #neo4jrestclient
            def _hash_(self):
                return hash(self.url)
            try:
                index = conn.nodes.indexes.get(index_name)
            except:
                index = conn.nodes.indexes.create(index_name, type='fulltext')
            index.__hash__ = _hash_.__get__(index, neo_client.Index)
            return index
        cls._indexes[cls][using] = index = get_index(index_name)

        return index

    @classmethod
    def index_name(cls, using=DEFAULT_DB_ALIAS):
        if cls in cls._indexes:
            if using in cls._indexes:
                return cls._indexes[cls][using]
        else:
            cls._indexes[cls] = {}

        model_parents = [t for t in cls.mro() if issubclass(t, NodeModel) and t is not NodeModel]
        if len(model_parents) == 0:
            #because marking this method abstract with the django metaclasses
            #is tough
            raise NotImplementedError('Indexing a base NodeModel is not '
                                      'implemented.')
        elif len(model_parents) > 1:
            return model_parents[-1].index_name(using=using)

        return"{0}-{1}".format(cls._meta.app_label, cls.__name__,)

    @property
    def connection(self):
        return connections[self.using]

    @alters_data
    @transactional
    def delete(self):
        if self.__node is None:
            raise ValueError("Unsaved nodes can't be deleted.")
        for rel in self.__node.relationships.all():
            rel.delete()
        cls = self.__class__
        signals.pre_delete.send(sender=cls, instance=self, using=self.using)
        self.__node.delete()
        signals.post_delete.send(sender=cls, instance=self, using=self.using)
        self.__node = None

    @alters_data
    @not_implemented
    @transactional
    def _insert(self, values, **kwargs):  # XXX: what is this?
        pass

    __node = None

    @property
    def node(self):
        node = self.__node
        if node is None:
            raise ValueError("Unsaved models don't have underlying nodes.")
        else:
            return node

    def save(self, using=DEFAULT_DB_ALIAS, **kwargs):
        return super(NodeModel, self).save(using=using, **kwargs)

    @alters_data
    #@transactional
    def save_base(self, raw=False, cls=None, origin=None,
                  force_insert=False, force_update=False,
                  using=DEFAULT_DB_ALIAS, *args, **kwargs):
        assert not (force_insert and force_update)
        using = using or DEFAULT_DB_ALIAS
        self.__using = using

        if cls is None:
            cls = self.__class__
        signals.pre_save.send(sender=cls, instance=self, raw=raw, using=using)

        is_new = self.id is None
        self._save_neo4j_node(using)
        self._save_properties(self, self.__node, is_new)
        self._save_neo4j_relationships(self, self.__node)

        signals.post_save.send(sender=cls, instance=self, created=(not is_new),
                               raw=raw, using=using)

    @alters_data
    @transactional
    def _save_neo4j_node(self, using):
        #if the node hasn't been created, do that
        if self.id is None:
            #TODO #244, batch optimization
            #get all the type props, in case a new type node needs to be created
            type_hier_props = [{'app_label': t._meta.app_label,
                                'model_name': t.__name__} for t in self._concrete_type_chain()]
            type_hier_props = list(reversed(type_hier_props))
            #get all the names of all types, including abstract, for indexing
            type_names_to_index = [t._type_name() for t in type(self).mro()
                                   if (issubclass(t, NodeModel) and t is not NodeModel)]
            script = '''
            node = Neo4Django.createNodeWithTypes(types)
            Neo4Django.indexNodeAsTypes(node, indexName, typesToIndex)
            results = node
            '''
            conn = connections[using]
            self.__node = conn.gremlin_tx(script, types=type_hier_props,
                                          indexName=self.index_name(),
                                          typesToIndex=type_names_to_index)
        return self.__node

    @classmethod
    def _concrete_type_chain(cls):
        """
        Returns an iterable of this NodeModel's concrete model ancestors,
        including itself, from newest (this class) to oldest ancestor.
        """
        def model_parents(cls):
            cur_cls = cls
            while True:
                bases = filter(lambda c: issubclass(c, NodeModel), cur_cls.__bases__)
                if len(bases) > 1:
                    raise ValueError('Multiple inheritance of NodeModels is not currently supported.')
                elif len(bases) == 0:
                    return
                cur_cls = bases[0]
                if not cur_cls._meta.abstract:
                    yield cur_cls
        return itertools.chain([cls], model_parents(cls))

    #XXX: conditionally memoized classmethod
    def __type_node(cls, using):
        conn = connections[using]
        name = cls.__name__

        type_hier_props = [{'app_label': t._meta.app_label, 'model_name': t.__name__}
                           for t in cls._concrete_type_chain()]
        type_hier_props = list(reversed(type_hier_props))
        script = "results = Neo4Django.getTypeNode(types)"
        error_message = 'The type node for class %s could not be created in the database.' % name
        try:
            script_rv = conn.gremlin_tx(script, types=type_hier_props)
        except Exception, e:
            raise RuntimeError(error_message, e)
        if not hasattr(script_rv, 'properties'):
            raise RuntimeError(error_message + '\n\n%s' % script_rv)
        return script_rv

    __type_node_memoized = classmethod(memoized(__type_node))
    __type_node_classmethod = classmethod(__type_node)

    @classmethod
    def _type_node(cls, using):
        """
        Switch between memoized and classmethod when attribute is accessed
        """
        if not (settings.DEBUG or
                getattr(settings, 'RUNNING_NEO4J_TESTS', None)):
            return cls.__type_node_memoized(using)
        else:
            return cls.__type_node_classmethod(using)

    @classmethod
    def _type_name(cls):
        return '{0}:{1}'.format(cls._meta.app_label, cls.__name__)

    @classmethod
    def _root_type_node(cls, using):
        #TODO consider moving to inferring this from the python inheritance
        #tree, not from the graph structure
        type_node = cls._type_node(cls, using)
        traversal = type_node.traverse(
            types=[neo_client.Incoming.get('<<TYPE>>')],
            uniqueness=neo_constants.NODE_GLOBAL,
            stop=neo_constants.STOP_AT_END_OF_GRAPH)
        #since -1 should be the reference node...
        return traversal[-2]

    @not_implemented
    @transactional
    def _get_next_or_previous_by_FIELD(self, field, is_next, **kwargs):
        pass

    @not_implemented
    @transactional
    def _get_next_or_previous_in_order(self, is_next):
        pass

    @not_supported
    def _collect_sub_objects(self, seen_objs, parent=None, nullable=False):
        pass
