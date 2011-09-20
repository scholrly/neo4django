from django.db import models as dj_models

import neo4jrestclient.client as neo_client
import neo4jrestclient.constants as neo_constants

from neo4django.db import connections, DEFAULT_DB_ALIAS
from neo4django.exceptions import NoSuchDatabaseError
from neo4django.decorators import not_implemented, alters_data, transactional, not_supported
from neo4django.constants import TYPE_ATTR

from manager import NodeModelManager

class IdProperty(object):
    def __init__(self, getter, setter):
        self.getter = getter
        self.setter = setter
    def __get__(self, inst, cls):
        if inst is None: return IdLookup(cls)
        else:
            return self.getter(inst)
    def __set__(self, inst, value):
        return self.setter(inst, value)

class IdLookup(object):
    indexed = True
    unique = True
    def __init__(self, model):
        self.__model = model
    index = property(lambda self: self)
    
    def to_neo(self, value):
        return int(value)
    
    def nodes(self, nodeid):
        #TODO is this dead code?
        try:
            node = connections[self.__model.__using].node[nodeid]
        except:
            node = None
        else:
            app_label = self.__model._meta.app_label
            model_name = self.__model.__name__

            #we only support single inheritance - first NodeModel descendant in the hierarchy wins
            parents = [cls for cls in type(self.__model).__bases__
                        if issubclass(cls, NodeModel) and cls is not NodeModel]

            if parents:
                parent_label = parents[0]._meta.app_label
                parent_model = parents[0].__name__
            else:
                parent_label = parent_model = None

            type_node = connections[self.__model.using].type_node(
                app_label, model_name, parent_label, parent_model)
            for rel in node.relationships.incoming('<<INSTANCE>>'):
                # verify that the found node is an instance of the
                # requested type
                if rel.start == type_node: break # ok, it is!
            else: # no, it isn't!
                node = None
        if node is not None:
            yield node

class NeoModelBase(dj_models.Model.__metaclass__):
    """
    Model metaclass that adds creation counters to models, and a hook for
    adding custom "class Meta" style options to NeoModels beyond those
    supported by Django.
    """
    meta_additions = ['has_own_index']

    def __init__(cls, name, bases, dct):
        super(NeoModelBase, cls).__init__(name, bases, dct)
        cls._creation_counter = 0
        
    def __new__(cls, name, bases, attrs):
        super_new = super(NeoModelBase, cls).__new__
        attr_meta = attrs.get('Meta', None)
        extra_options = {}
        if attr_meta:
            for key in NeoModelBase.meta_additions:
                if hasattr(attr_meta, key):
                    extra_options[key] = getattr(attr_meta, key)
                    delattr(attr_meta, key)
        new_cls = super_new(cls, name, bases, attrs)
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
        names = [name_db_pair[1] for name_db_pair in connections.iteritems() 
            if name_db_pair[1].url in neo_node.url]
        if len(names) < 1:
            raise NoSuchDatabaseError(url=neo_node.url)

        instance.__using = names[0]

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
        pk1 = self._get_pk_val()
        pk2 = self._get_pk_val()
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
            if name not in onto_field_names or name in ('pk', 'id'): continue
            val = getattr(neo_model, name)
            if isinstance(val, dj_models.Manager):
                for obj in val.all():
                    getattr(new_model, name).add(obj)
            else:
                setattr(new_model, name, val)
        return new_model

    @classmethod
    def index(cls, using=DEFAULT_DB_ALIAS):
        if using in cls._indexes:
            return cls._indexes[using]
        
        model_parents = [t for t in cls.mro() \
                            if issubclass(t, NodeModel) and t is not NodeModel]
        if len(model_parents) == 0:
            #because marking this method abstract with the django metaclasses
            #is tough
            raise NotImplementedError('Indexing a base NodeModel is not '
                                      'implemented.')
        elif len(model_parents) > 1:
            return model_parents[-1].index(using=using)

        conn = connections[using]
        index_name = "{0}-{1}".format(
            cls._meta.app_label,
            cls.__name__,)
        try:
            cls._indexes[using] = index = conn.nodes.indexes.get(index_name)
        except:
            cls._indexes[using] = index = conn.nodes.indexes \
                    .create(index_name, type='fulltext')
        return index

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
        self.__node.delete()
        self.__node = None

    @alters_data
    @not_implemented
    @transactional
    def _insert(self, values, **kwargs):  ##XXX: what is this?
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
    @transactional
    def save_base(self, raw=False, cls=None, origin=None,
                  force_insert=False, force_update=False,
                  using=DEFAULT_DB_ALIAS, *args, **kwargs):
        assert not (force_insert and force_update)
        self.__using = using

        is_new = self.__node is None
        self._save_neo4j_node(using)
        self._save_properties(self, self.__node, is_new)
        self._save_neo4j_relationships(self, self.__node)

    @alters_data
    @transactional
    def _save_neo4j_node(self, using):
        #if the node hasn't been created, do that
        if self.__node is None:
            #TODO #244, batch optimization
            self.__node = node = connections[using].node()
            #and attach it to the subreference nodes we're using to express
            #node type in the graph
            self._type_node(using).relationships.create('<<INSTANCE>>', node)
            types_to_index = [t for t in type(self).mro() \
                              if issubclass(t, NodeModel) and t is not NodeModel]
            for t in types_to_index:
                self.index(using=using).add(TYPE_ATTR, t._type_name(), node)
        return self.__node

    #TODO memoize
    @classmethod
    def _type_node(cls, using):
        conn = connections[using]
        app_label = cls._meta.app_label
        model_name = cls.__name__

        def find_type_node(app_label, model_name):
            traversal = conn.reference_node.traverse(
                    types=[neo_client.Outgoing.get('<<TYPE>>')],
                    uniqueness=neo_constants.NODE_GLOBAL,
                    stop=neo_constants.STOP_AT_END_OF_GRAPH)
            possible_nodes = [n for n in traversal
                                if n.get('app_label', None) == app_label and \
                                n.get('model_name', None) == model_name]
            if len(possible_nodes) == 0:
                return None
            elif len(possible_nodes) == 1:
                return possible_nodes[0]
            else:
                raise ValueError("There were multiple type nodes found for the"
                                 " app_label and model_name - looks like your "
                                 "graph might be messed up.")

        node = find_type_node(app_label, model_name)
        if node is not None:
            return node
        
        node = conn.node()
        node['app_label'] = app_label
        node['model_name'] = model_name
        
        parents = [c for c in cls.__bases__
                        if issubclass(c, NodeModel) and c is not NodeModel]

        if len(parents)>1:
            #XXX: only supports single inheritance right now
            raise ValueError('Multiple inheritance of NodeModels is not currently'
                             'supported.')
        elif len(parents)==1:
            parent_node = parents[0]._type_node(using)
            parent_node.relationships.create("<<TYPE>>", node)
            pass
        else:
            conn.reference_node.relationships.create("<<TYPE>>", node)

        node['name'] = '[{0}]'.format(cls._type_name())
        return node

    @classmethod
    def _all_instance_nodes(cls, using):
        #return all traversed instance nodes, including subtype instances
        #TODO!!
        pass

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
    def _collect_sub_objects(self,seen_objs,parent=None,nullable=False):
        pass
