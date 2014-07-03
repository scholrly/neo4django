from django.db import models
from django.db.models.fields.related import add_lazy_relation
from django.db.models.query_utils import DeferredAttribute
from django.db.models.query import EmptyQuerySet
from django.db.models.signals import post_delete
from django.forms import ModelChoiceField, ModelMultipleChoiceField
from django.utils.text import capfirst
from django.dispatch import receiver

from neo4django import Incoming, Outgoing
from neo4django.db import DEFAULT_DB_ALIAS
from neo4django.decorators import not_implemented, transactional
from neo4django.utils import AssignableList, AttrRouter
from neo4django.constants import INTERNAL_ATTR, ORDER_ATTR
from .base import NodeModel
from .query import (NodeQuerySet, Query, cypher_rel_str)
from .cypher import  (Clauses, Start, With, Match, Path, NodeComponent,
        RelationshipComponent, OrderBy, OrderByTerm, ColumnExpression)

from neo4jrestclient.constants import RELATIONSHIPS_IN, RELATIONSHIPS_OUT

from collections import defaultdict
from functools import partial


class RelationshipModel(object):
    """
    Model backing all relationships. Intended for a single instance to
    correspond to an edge in the graph.
    """
    __relationship = None

    def __init__(self):
        pass

    @property
    def relationship(self):
        rel = self.__relationship
        if rel is None:
            # XXX: better exception
            raise ValueError("Unsaved objects have no relationship.")
        return rel
    _neo4j_underlying = relationship

    @classmethod
    def new(RelationshipModel, module, name, bases):
        return type(name, bases + (RelationshipModel,),
                    {'__module__': module})

    @not_implemented
    @classmethod
    def add_field(self, prop):
        raise NotImplementedError("<RelationshipModel>.add_field()")


class Relationship(object):

    def __init__(self, target, rel_type=None, direction=None, optional=True,
                 single=False, related_single=False, related_name=None,
                 editable=True, verbose_name=None, help_text=None,
                 preserve_ordering=False, null = True, metadata={},
                 rel_metadata={}):
        if direction is Outgoing:
            direction = RELATIONSHIPS_OUT
        elif direction is Incoming:
            direction = RELATIONSHIPS_IN
        elif direction is None:
            if not isinstance(rel_type, basestring):
                direction = rel_type.direction
            else:
                direction = RELATIONSHIPS_OUT
        if not isinstance(rel_type, basestring):
            if rel_type.direction != direction:
                raise ValueError("Incompatible direction!")
            rel_type = rel_type.type

        self._reverse_relationship_type = Relationship

        self.__target = target
        self.name = rel_type
        self.__single = single
        self.direction = direction
        self.__related_single = related_single
        self._related_name = related_name
        self.__ordered = preserve_ordering
        self.__meta = metadata
        self.__related_meta = rel_metadata
        self.editable = editable
        self.optional = optional
        self.verbose_name = verbose_name
        self.help_text = help_text
        self.null = null

    target_model = property(lambda self: self.__target)
    ordered = property(lambda self: self.__ordered)
    meta = property(lambda self: self.__meta)
    single = property(lambda self: self.__single)

    __is_reversed = False

    def reverse(self, target, name):
        if self.direction is RELATIONSHIPS_IN:
            direction = RELATIONSHIPS_OUT
        elif self.direction is RELATIONSHIPS_OUT:
            direction = RELATIONSHIPS_IN
        else:
            direction = RELATIONSHIPS_OUT
        Type = self._reverse_relationship_type
        relationship = Type(
            target, rel_type=self.name, direction=direction,
            single=self.__related_single, related_name=name,
            metadata=self.__related_meta, preserve_ordering=self.__ordered)
        relationship.__is_reversed = True
        return relationship

    def reversed_name(self, target=None):
        if self._related_name:
            return self._related_name
        else:
            return self.get_name(target, self.__single)

    @staticmethod
    def get_name(target, single=False):
        suffix = '%s' if single else '%s_set'
        if isinstance(target, basestring):
            name = target.rsplit('.', 1)[-1]
        else:
            name = target.__name__
        return suffix % name.lower()

    def get_internal_type(self):
        return "Neo4jRelationship"

    def has_default(self):
        return False

    def _get_bound_relationship_type(self):
        # TODO this will change with relationship models (#1)
        if self.__single:
            return SingleNode
        else:
            return MultipleNodes

    def _get_new_bound_relationship(self, source, name):
        return self._get_bound_relationship_type()(self, source, self.name or name, name)

    def contribute_to_class(self, source, name):
        if not issubclass(source, NodeModel):
            raise TypeError("Relationships may only extend from Nodes.")
        self.creation_counter = source.creation_counter

        # XXX this is to cover strange situations like accidental overriding
        # of abstract models' reverse relationships like issue #190
        if hasattr(source, name):
            return

        # make sure this relationship doesn't overlap with another of the same
        # type and direction
        if hasattr(source._meta, '_relationships'):
            for r in source._meta._relationships.values():
                if r.rel_type == self.name and r.direction == self.direction:
                    import warnings
                    warnings.warn('`%s` and `%s` share a relationship type and '
                                  'direction. Is this what you meant to do?'
                                  % (r.name, name))
        bound = self._get_new_bound_relationship(source, name)
        source._meta.add_field(bound)
        if not hasattr(source._meta, '_relationships'):
            source._meta._relationships = {}
        source._meta._relationships[name] = bound
        setattr(source, name, bound)
        if isinstance(self.__target, basestring):
            def setup(field, target, source):
                if not issubclass(target, NodeModel):
                    raise TypeError("Relationships may only extend from Nodes.")
                # replace the string target with the real target
                self.__target = target
                bound._setup_reversed(target)
            add_lazy_relation(source, self, self.__target, setup)
        target = self.__target
        if not self.__is_reversed:
            bound._setup_reversed(target)

    def formfield(self, **kwargs):
        defaults = {
            'required': not self.optional,
            'label': capfirst(self.verbose_name),
            'help_text': self.help_text,
            'queryset': self.target_model.objects
        }
        if self.single:
            defaults['form_class'] = ModelChoiceField
        else:
            defaults['form_class'] = ModelMultipleChoiceField
        defaults.update(kwargs)

        form_class = defaults['form_class']
        del defaults['form_class']
        return form_class(**defaults)

    ###################
    # SPECIAL METHODS #
    ###################

    def __getinitargs__(self):
        return (self.__target, self.name, self.direction, True, self.__single,
                self.__related_single, self.reversed_name, self.__ordered,
                self.__meta, self.__related_meta)


#subclasses DeferredAttribute to avoid being set to None in
#django.db.models.Model.__init__().
class BoundRelationship(AttrRouter, DeferredAttribute):
    indexed = False
    rel = None
    primary_key = False
    choices = None
    db_index = None

    blank = True
    unique = False
    unique_for_date = False
    unique_for_year = False
    unique_for_month = False

    def __init__(self, rel, source, relname, attname, serialize=True):
        self.__rel = rel
        self.__source = source
        self._type = relname
        self.__attname = attname
        self.serialize = serialize
        relationships = self._relationships_for(source)
        relationships[self.__attname] = self  # XXX weakref
        self._route(['reversed_name',
                     'direction',
                     'target_model',
                     'ordered',
                     'help_text',
                     'meta',
                     'get_internal_type',
                     'help_text',
                     'verbose_name',
                     'null',
                     'has_default',
                     # form handling
                     'editable',
                     'formfield',
                     ], self.__rel)
        self.null = False

    def clean(self, value, instance):
        return value

    def has_default(self):
        return None

    def get_internal_type(self):
        return self.__rel.__class__.__name__

    def _setup_reversed(self, target):
        self.__target = target
        if not isinstance(target, basestring):
            self.__rel.reverse(self.__source,
                               self.__attname).contribute_to_class(
                                   target, self.reversed_name(self.__source))

    attname = name = property(lambda self: self.__attname)

    @property
    def rel_type(self):
        return self._type

    @property
    def relationship(self):
        return self.__rel

    @property
    def target_model(self):
        return self.__target

    @property
    def source_model(self):
        return self.__source

    def get_default(self):
        return None

    def contribute_to_class(self, source, name):
        return self.__rel.contribute_to_class(source, name)

    def _get_val_from_obj(self, obj):
        return self.__get__(obj)

    def value_to_string(self, obj):
        return str(self.__get__(self))

    @staticmethod
    def _state_for(instance, create=True):
        try:
            state = instance.__state
        except:
            state = {}
            if create:
                instance.__state = state
        return state

    @staticmethod
    def _relationships_for(obj_or_cls):
        meta = obj_or_cls._meta
        try:
            relationships = meta._relationships
        except:
            meta._relationships = relationships = {}
        return relationships

    @staticmethod
    def _all_relationships_for(obj_or_cls):
        new_rel_dict = {}
        new_rel_dict.update(BoundRelationship._relationships_for(obj_or_cls))

        cls = obj_or_cls if isinstance(obj_or_cls, type) else type(obj_or_cls)

        for parent in cls.__bases__:
            if hasattr(parent, '_meta'):
                new_rel_dict.update(BoundRelationship._all_relationships_for(parent))

        return new_rel_dict

    def _save_(instance, node):
        state = BoundRelationship._state_for(instance, create=False)
        if state:
            rels = BoundRelationship._all_relationships_for(instance)
            for key in state.keys():
                rels[key]._save_relationship(instance, node, state[key])
                if isinstance(state[key], tuple):  # HACK, rearchitect
                    state[key] = (False, state[key][1])

    #TODO this... well, consider revising
    NodeModel._save_neo4j_relationships = staticmethod(_save_)

    @not_implemented
    def _save_relationship(self, instance, node, state):
        pass

    def _load_relationships(self, node):
        #Returns all neo4j relationships attached to the provided neo4j node.
        #TODO TODO we can probably trash this function with the new backend, refs 174
        if self.direction is RELATIONSHIPS_OUT:
            rel_func = node.relationships.outgoing
        else:
            rel_func = node.relationships.incoming
        rels = rel_func([self._type])
        if not hasattr(self, '_relationships'):
            attrs = {}
        else:
            attrs = self._relationships.get_new_attrs()

        self._relationships = AssignableList(rels)
        for k in attrs.keys():
            setattr(self._relationships, k, attrs[k])
        return self._relationships

    creation_counter = property(lambda self: self.__rel.creation_counter)

    def _set_relationship(self, obj, state, value):
        if value is None:  # assume initialization - ignore
            return  # TODO: verify that obj is unsaved!
        raise TypeError("<%s>.%s is not assignable" %
                        (obj.__class__.__name__, self.name))

    def _del_relationship(self, obj, state):
        raise TypeError("Cannot delete <%s>.%s" %
                        (obj.__class__.__name__, self.name))

    def _create_neo_relationship(self, node, obj, **kwargs):
        neo_rel_attrs = kwargs.get('attrs', {})
        neo_rel_attrs[INTERNAL_ATTR] = True
        if obj.pk is None:
            obj.save(using=obj.using)
        other = obj.node
        # TODO: verify that it's ok in the reverse direction?
        if self.direction != 'out':
            node, other = other, node

        node.relationships.create(self._type, other, **neo_rel_attrs)

    @classmethod
    def to_python(cls, value):
        """
        A python-centric alias for from_neo()
        """
        return cls.from_neo(value)

    @classmethod
    def from_python(cls, value):
        """
        A python-centric alias for to_neo()
        """
        return cls.to_neo(value)

    @classmethod
    def to_neo(cls, value):
        return value

    @classmethod
    def from_neo(cls, value):
        return value

    ###################
    # SPECIAL METHODS #
    ###################

    def __getinitargs__(self):
        return (self.__rel, self.__source, self.__type, self.__attname, self.serialize)

    def __cmp__(self, other):
        return cmp(self.creation_counter, other.creation_counter)

    ######################
    # DESCRIPTOR METHODS #
    ######################

    def __get__(self, obj, cls=None):
        if obj is None:
            return self
        return self._get_relationship(obj, self._state_for(obj))

    def __set__(self, obj, value):
        self._set_relationship(obj, self._state_for(obj), value)

    def __delete__(self, obj):
        self._del_relationship(obj, self._state_for(obj))


class SingleNode(BoundRelationship):
    #BoundRelationship subclass for a single node relationship without an
    #associated relationship model.
    def _get_relationship(self, obj, state):
        if self.name in state:
            changed, result = state[self.name]
            return result

        if hasattr(obj, 'node'):
            this = obj.node
        else:
            return None

        result = self._load_related(this)
        state[self.name] = False, result
        return result

    def value_from_object(self, obj):
        return self.__get__(obj)

    @transactional
    def _load_related(self, node):
        relationships = self._load_relationships(node)
        #TODO seriously consider removing this restriction- I'm not sure I see
        # any benefit, and it makes creating neo4django-compliant graphs that
        # much more difficult.
        django_relationships = filter(lambda rel: rel[INTERNAL_ATTR], relationships)
        if len(django_relationships) < 1:
            return None
        elif len(django_relationships) > 1:
            raise ValueError("There's an ambiguous relationship set in the "
                             "database from node %d - there should only be one"
                             " relationship flagged as '_neo4django' for a "
                             "single=True Relationship." % node.id)
        return self._neo4j_instance(node, django_relationships[0])

    def _neo4j_instance(self, this, relationship):
        if this.id == relationship.end.id:
            that = relationship.start
        else:
            that = relationship.end  # get the other node

        return self.target_model._neo4j_instance(that)

    def _del_relationship(self, obj, state):
        self._set_relationship(obj, state, None)

    def _set_relationship(self, obj, state, other):
        state[self.name] = True, other

    def _save_relationship(self, instance, node, state):
        changed, other = state
        if not changed:
            return
        rels = self._load_relationships(node)

        #delete old relationship
        #create new relationship

        if other is None:
            #delete old relationship if it exists
            if hasattr(rels, 'single') and rels.single:
                rels.single.delete()  # TODO this deletion should be transactional w creation
            rels.single = None
        else:
            rels.single = self._create_neo_relationship(node, other)
            #other._save_neo4j_node(DEFAULT_DB_ALIAS)

    def save_form_data(self, instance, data):
        # TODO we need a function like _get_relationship that only takes a
        # model instance...
        state = self._state_for(instance)
        self._set_relationship(instance, state, data)

    def _set_cached_relationship(self, obj, other):
        state = BoundRelationship._state_for(obj)
        if self.name in state and state[self.name]:
            raise ValueError("Can't set the cache on an already initialized relationship!")
        state[self.name] = False, other


class BoundRelationshipModel(BoundRelationship):
    def __init__(self, rel, cls, relname, attname, Model):
        super(BoundRelationship, self).__init__(
            rel, cls, relname, attname)
        self.Model = Model
        raise NotImplementedError("Support for extended relationship "
                                  "models is not yet implemented.")


class SingleRelationship(BoundRelationshipModel):  # WAIT!

    @not_implemented
    def _get_relationship(self, obj, state):
        pass

    @not_implemented
    def _set_relationship(self, obj, state, other):
        pass


class MultipleNodes(BoundRelationship):
    #BoundRelationship subclass for a multi-node relationship without an
    #associated relationship model.

    def value_from_object(self, obj):
        return self.__get__(obj).all()

    def value_to_string(self, obj):
        return str([item.pk for item in list(self.__get__(obj).all())])

    def save_form_data(self, instance, data):
        # TODO we need a function like _get_relationship that only takes a
        # model instance...
        states = self._state_for(instance)
        self._set_relationship(instance, states, list(data))

    def clean(self, value, instance):
        # XXX HACK since we don't use a proxy object like
        # ForeignRelatedObjectsDescriptor (and actually return a
        # RelationshipInstance on getattr(model, field.attname)) so we have to
        # unpack a RelationshipInstance
        return list(value._added)

    def _get_state(self, obj, states):
        state = states.get(self.name)
        if state is None:
            states[self.name] = state = RelationshipInstance(self, obj)
        return state

    def _get_relationship(self, obj, states):
        return self._get_state(obj, states)

    def _set_relationship(self, obj, states, value):
        if value is not None:
            state = self._get_state(obj, states)
            items = list(state.all())
            notsaved = state._added
            if items and len(notsaved) < len(items):
                state.remove(*items)
                #TODO: make it so it only removes authors not in value
                #      and remove authors already there from value
                #XXX: only works when removing from saved nodes
            if notsaved:
                notsaved[:] = []  # TODO give rel instance a method for this?
            if hasattr(value, '__iter__'):
                state.add(*value)
            else:
                state.add(value)

    def _neo4j_instance(self, this, relationship):
        if this.id == relationship.start.id:
            that = relationship.end
        else:
            that = relationship.start
        return self.target_model._neo4j_instance(that)

    def accept(self, obj):
        pass  # TODO: implement verification

    def _save_relationship(self, instance, node, state):
        state.__save__(node)

    def _load_relationships(self, node, ordered=False, **kwargs):
        sup = super(MultipleNodes, self)._load_relationships(node, **kwargs)
        if ordered:
            return sorted(sup, key=lambda rel: rel[ORDER_ATTR])
        return sup

    def _create_neo_relationship(self, node, *args, **kwargs):
        if self.ordered:
            rels = self._load_relationships(node, ordered=True, **kwargs)
            new_index = rels[-1][ORDER_ATTR] + 1 if len(rels) > 0 else 0
            if 'attrs' in kwargs:
                kwargs['attrs'][ORDER_ATTR] = new_index
            else:
                kwargs['attrs'] = {ORDER_ATTR: new_index}
        return super(MultipleNodes, self)._create_neo_relationship(node, *args, **kwargs)


class MultipleRelationships(BoundRelationshipModel):  # WAIT!

    @not_implemented
    def _get_relationship(self, obj, state):
        pass

    @not_implemented
    def add(self, obj, other):
        pass


# TODO this needs to be supplanted by using somthing like django.db.models
# .fields.related.ForeignRelatedObjectsDescriptor
class RelationshipInstance(models.Manager):
    """
    A manager that keeps state for the many side (`MultipleNodes`) of
    relationships.
    """
    def __init__(self, rel, obj):
        self._rel = rel
        self._obj = obj
        self._added = []  # contains domain objects
        self._removed = []  # contains relationships
        #holds cached domain objects (that have been added or loaded by query)
        self._cache = None
        self._cache_unique = set([])

        # sender should be the associated model (not any associated LazyModel)
        sender = (self._rel.target_model._model
                  if hasattr(self._rel.target_model, '_model')
                  else self._rel.target_model)

        @receiver(post_delete, sender=sender, weak=False)
        def delete_handler(sender, **kwargs):
            deleted_obj = kwargs.pop('instance', None)
            if deleted_obj:
                self._remove_from_cache(deleted_obj)
                if deleted_obj in self._added:
                    self._added.remove(deleted_obj)

    ordered = property(lambda self: self._rel.ordered)

    def _add_to_cache(self, *relationship_neo4j_pairs):
        for pair in relationship_neo4j_pairs:
            if pair not in self._cache_unique:
                self._get_or_create_cache().append(pair)
                self._cache_unique.add(pair)

    def _remove_from_cache(self, obj):
        if self._cache is not None:
            for r, cached_obj in self._cache[:]:
                if cached_obj == obj:
                    pair = (r, cached_obj)
                    self._cache.remove(pair)
                    self._cache_unique.remove(pair)
                    break

    def _has_cache(self):
        return self._cache is not None

    def _get_or_create_cache(self):
        if self._cache is None:
            self._cache = []
        return self._cache

    def __save__(self, node):
        #Deletes all relationships removed since last save and adds any new
        #relatonships to the database.

        #TODO this should be batched
        for relationship in self._removed:
            relationship.delete()
        for obj in self._added:
            new_rel = self._rel._create_neo_relationship(node, obj)
            self._add_to_cache((new_rel, obj))
        self._removed[:] = []
        self._added[:] = []

    def _neo4j_relationships_and_models(self, node):
        """
        "Returns generator of relationship, neo4j instance tuples associated
        with node.
        """
        if self._cache is None:
            self._add_to_cache(*[(r, self._rel._neo4j_instance(node, r)) for r in
                               self._rel._load_relationships(node, ordered=self.ordered)])
        for tup in self._get_or_create_cache():
            if tup[0] not in self._removed:
                yield tup

    @property
    def _new(self):
        for item in self._added:
            yield item

    @property
    def _old(self):
        for item in self._removed:
            yield item

    def add(self, *objs):
        """
        Adds object(s) to the relationship. If ordered is True for
        the relationship, these objects will all be put at the end of the line.
        """
        for obj in objs:
            self._rel.accept(obj)
        self._added.extend(objs)

    def remove(self, *objs):
        """
        Remove objects from the relationship. If ordered is True,
        remove the first relationship to this object- otherwise, remove one
        of the relationships indiscriminately.
        """
        rel = self._rel
        if hasattr(self._obj, 'node'):
            neo_rels = list(rel._load_relationships(self._obj.node,
                                                    ordered=self.ordered))
            rels_by_node = defaultdict(list)
            for neo_rel in neo_rels:
                rels_by_node[neo_rel.start.url].append(neo_rel)
                rels_by_node[neo_rel.end.url].append(neo_rel)
                
            for obj in objs:
                candidate_rels = rels_by_node[obj.node.url] if hasattr(obj, 'node') else []
                if candidate_rels:
                    if candidate_rels[0] not in self._removed:
                        self._removed.append(candidate_rels.pop(0))
                else:
                    try:
                        self._added.remove(obj)
                    except ValueError:
                        raise rel.target_model.DoesNotExist(
                            "%r is not related to %r." % (obj, self._obj))
                self._remove_from_cache(obj)
        else:
            for obj in objs:
                try:
                    if obj in self._added:
                        self._added.remove(obj)
                    elif obj in self._cache:
                        self._remove_from_cache(obj)
                except ValueError:
                    raise rel.target_model.DoesNotExist(
                        "%r is not related to %r." % (obj, self._obj))

    def clear(self):
        all_objs = list(self.all())
        self.remove(*all_objs)

    def clone(self):
        # Should cache be updated as well?
        cloned = RelationshipInstance(self._rel, self._obj)
        cloned.add(*self._new)
        return cloned

    def create(self, **kwargs):
        kwargs[self._rel.relationship._related_name] = self._obj
        new_model = self._rel.relationship.target_model(**kwargs)
        # TODO: saving twice, should only need
        # to save self._obj after #89 fix
        new_model.save()
        self._obj.save()

    @not_implemented
    def get_or_create(self, *args, **kwargs):
        pass

    def get_query_set(self):
        return RelationshipQuerySet(self, self._rel, self._obj)

    def get_empty_query_set(self):
        return EmptyQuerySet()


class RelationshipQuerySet(NodeQuerySet):

    def __init__(self, rel_instance, rel, model_instance, model=None,
                 query=None, using=DEFAULT_DB_ALIAS):
        # TODO will cause issues with #138 - multi-typed relationships
        target_model = model or rel.relationship.target_model
        super(RelationshipQuerySet, self).__init__(
            model=target_model, query=query or Query(target_model),
            using=using)
        self._rel_instance = rel_instance
        self._rel = rel
        self._model_instance = model_instance

        self.query.set_start_clause(self._get_start_clause(), lambda: {
            'startParam': self._model_instance.id
        })

    def _get_start_clause(self):
        """
        Return a Cypher START fragment - either a str, or an object with an
        as_cypher() method -  that will be used as the first half of the query
        built executing the query set. The query should expect a parameter named
        "startParam" containing this side of the relationship's node id, and
        should define a column "n" containing nodes to later be filtered
        against.
        """
        order_clause = """
            ORDER BY r.`%s`
        """ % ORDER_ATTR if self._rel_instance.ordered else ''

        start = Start({'m': 'node({startParam})'}, ['startParam'])

        direction = '>' if self._rel.direction == RELATIONSHIPS_OUT else '<'

        match = Match([
            Path([NodeComponent('m'),
                  RelationshipComponent(identifier='r',
                                        types=[self._rel.rel_type],
                                        direction=direction),
                  NodeComponent('n')])])

        order_by = OrderBy([OrderByTerm(ColumnExpression('r', ORDER_ATTR))]) \
                if self._rel_instance.ordered else None

        return Clauses([
            start,
            match,
            With({'n': 'n', 'r': 'r', 'typeNode': 'typeNode'}, order_by=order_by)
        ])

    def iterator(self):
        added = list(self._rel_instance._new)
        if self._model_instance.id is not None:
            for m in super(RelationshipQuerySet, self).iterator():
                yield m
        for item in added:
            yield item

    def _clone(self, klass=None, setup=False, **kwargs):
        klass = klass or self.__class__
        klass = partial(klass, self._rel_instance, self._rel,
                        self._model_instance)
        return super(RelationshipQuerySet, self)._clone(klass=klass,
                                                        setup=setup, **kwargs)

    def count(self):
        removed = list(self._rel_instance._old)
        added = list(self._rel_instance._new)
        diff_len = max(len(added) - len(removed), 0)
        if self._model_instance.id is not None:
            return super(RelationshipQuerySet, self).count() + diff_len
        else:
            return diff_len
