from nose.tools import eq_, with_setup

def setup():
    global Person, neo4django, settings, gdb, models

    from neo4django.tests import Person, neo4django, gdb, models

def teardown():
    gdb.cleandb()

def test_basic_relationship():
    """
    Tests both sides of a simple many-to-many relationship (without relationship
    properties).
    """
    class RelatedPaper(models.NodeModel):
        authors = models.Relationship(Person,
                rel_type = neo4django.Outgoing.OWNED_BY,
                related_name = 'papers'
            )
    
    sandra = Person(name="Sandra")
    sandra.save()
    lifesWork = RelatedPaper()
    lifesWork.save()
    lifesWork.authors.add(sandra)
    
    lifesWork.save()
    work = list(sandra.papers.all())
    assert lifesWork in work, "Paper not found in %s" % repr(work)
    authors = list(lifesWork.authors.all())
    assert sandra in authors, "Author not found in %s" % repr(work)
    #find all shared neo4j relationships
    sandras = sandra.node.relationships.all(['OWNED_BY'])[:]
    eq_(len(sandras), 1)
    #test proper direction
    eq_(sandras[0].end, sandra.node)
    eq_(sandras[0].start, lifesWork.node)

def test_basic_relationship_manager():
    class SomeOtherPaper(models.NodeModel):
        authors = models.Relationship(Person,
                rel_type = neo4django.Outgoing.OWNED_BY,
                related_name = 'papers'
            )
    pete = Person.objects.create(name="PETE!")
    boring_paper = SomeOtherPaper()
    boring_paper.authors.add(pete)
    eq_(list(boring_paper.authors.all()), [pete])
    
    boring_paper.authors.remove(pete)
    eq_(list(boring_paper.authors.all()), [])
    
    other_paper = SomeOtherPaper.objects.create()
    other_paper.authors.add(pete)
    other_paper.authors.clear()
    eq_(list(other_paper.authors.all()), [])

    ## Test to make sure we don't end up with duplicates
    ## When we do two saves in a row after clearing
    other_paper.save()
    other_paper.authors.add(pete)
    other_paper.save()
    eq_(len(list(other_paper.authors.all())), 1)

def test_one_to_many():
    class Origin1(models.NodeModel):
        name = models.StringProperty()

    class Reference1(models.NodeModel):
        origin = models.Relationship(Origin1,
                                         rel_type=neo4django.Outgoing.REFERS_TO,
                                         related_name='references',
                                         single=True)

    origin = Origin1(name='CNN')
    origin.save()
    ref = Reference1()
    ref.origin = origin
    ref.save()
    assert ref.origin.name == origin.name, "The single side doesn't work!"
    assert len(list(origin.references.all())) == 1, \
            "Adding to the single side doesn't update the many side."

def test_many_to_one():
    class Origin2(models.NodeModel):
        name = models.StringProperty()

    class Reference2(models.NodeModel):
        origin = models.Relationship(Origin2,
                                         rel_type=neo4django.Outgoing.REFERS_TO,
                                         #TODO explore edge direction here, this is wrong
                                         related_name='references',
                                         single=True)
    origin = Origin2(name='CNN')
    origin.save()
    ref = Reference2()
    ref.save()
    origin.references.add(ref)
    origin.save()
    assert ref.origin and (ref.origin.name == origin.name), \
           "Adding to the many side doesn't update the single side."
    assert len(list(origin.references.all())) == 1, "The many side doesn't work!"

def test_related_one_to_many():
    class AnotherReference(models.NodeModel):
        pass

    class AnotherOrigin(models.NodeModel):
        name = models.StringProperty()
        references = models.Relationship(AnotherReference,
                                         rel_type=neo4django.Outgoing.REFERS_TO,
                                         related_name='origin',
                                         related_single=True)

    origin = AnotherOrigin(name='CNN')
    origin.save()
    ref = AnotherReference()
    ref.origin = origin
    ref.save()
    assert ref.origin.name == origin.name, "The single side doesn't work!"
    assert len(list(origin.references.all())) == 1, \
            "Adding to the single side doesn't update the many side."

def test_related_many_to_one():
    class AnotherReference1(models.NodeModel):
        pass

    class AnotherOrigin1(models.NodeModel):
        name = models.StringProperty()
        references = models.Relationship(AnotherReference1,
                                         rel_type=neo4django.Outgoing.REFERS_TO,
                                         related_name='origin',
                                         related_single=True)
    origin = AnotherOrigin1(name='CNN')
    ref = AnotherReference1()
    ref.save()
    ref2 = AnotherReference1()
    ref2.save()
    origin.references.add(ref)
    origin.references.add(ref2)
    origin.save()
    assert ref.origin and (ref.origin.name == origin.name), \
           "Adding to the many side doesn't update the single side."
    assert len(list(origin.references.all())) == 2, "The many side doesn't work!"

def test_one_to_one():
    class Stalker(models.NodeModel):
        name = models.StringProperty()
        person = models.Relationship(Person,
                                            rel_type=neo4django.Outgoing.POINTS_TO,
                                            single=True,
                                            related_single=True
                                        )
    p = Person.objects.create(name='Stalked')
    s = Stalker(name='Creeper')
    s.person = p
    s.save()

    #test that the one-to-one is correct after a retrieval
    new_s = list(Stalker.objects.all())[0]
    eq_(new_s.person, p)

def test_ordering():
    class Actor(models.NodeModel):
        name = models.StringProperty()
        def __str__(self):
            return self.name

    class MovieCredits(models.NodeModel):
        actors = models.Relationship(Actor,
                                         rel_type=neo4django.Incoming.ACTS_IN,
                                         related_name='movies',
                                         preserve_ordering=True,
                                        )

    actors = [Actor(name=n) for n in ['Johnny','Angelina','Jennifer','Tobey']]
    for a in actors: a.save()
    
    superhero_flick = MovieCredits()
    superhero_flick.save()
    for a in actors: superhero_flick.actors.add(a)
    superhero_flick.save()

    node = superhero_flick.node
    del superhero_flick
    same_flick = MovieCredits._neo4j_instance(node)
    assert actors == list(same_flick.actors.all())

    same_flick.actors.remove(actors[1])
    same_flick.save()
    del same_flick

    same_flick = MovieCredits._neo4j_instance(node)
    flick_actors = list(same_flick.actors.all())
    should_be = [actors[0]] + actors[2:]
    assert should_be == flick_actors, "%s should be %s" % (str(flick_actors), str(should_be))

def test_relationship_model():
    """Tests both sides of a many-to-many relationship with attached properties & model."""
    class Authorship(models.Relationship):
        when = models.DateProperty()
    class ComplexRelatedPaper(models.NodeModel):
        pass
    raise NotImplementedError("Write this test!")

def test_multinode_setting():
    """Tests setting a multi-node relationship directly instead of adding."""
    class Classroom(models.NodeModel):
        students = models.Relationship(Person,
                                rel_type=neo4django.Outgoing.COMES_TO,
                                related_name="school"
                                )
    class Student(models.NodeModel):
        name = models.StringProperty()
        def __str__(self):
            return self.name

    students = [Student(name=name) for name in ['Violet', 'Grigori', 'Kaden', 'Gluz']]
    classroom = Classroom()
    classroom.students = students[:2]
    assert len(list(classroom.students.all())) == 2
    classroom.students.add(students[2])
    assert len(list(classroom.students.all())) == 3
    classroom.students = students[3:]
    classroom.save()
    assert len(list(classroom.students.all())) == 1

def test_rel_metadata():
    class NodeWithRelMetadata(models.NodeModel):
        contacts = models.Relationship(Person,
                                           rel_type=neo4django.Outgoing.KNOWS,
                                           metadata={'test':123})
    meta_fields = filter(lambda f: hasattr(f, 'meta'), NodeWithRelMetadata._meta.fields)
    eq_(len(meta_fields), 1)
    assert 'test' in meta_fields[0].meta
    eq_(meta_fields[0].meta['test'], 123)

def test_rel_self():
    class MetaNode(models.NodeModel):
        myself = models.Relationship('self', 'IS', single=True, related_name = 'myselves')

    meta = MetaNode()
    meta.myself = meta
    meta.save()

    eq_(meta.myself, meta)
    assert meta in meta.myselves.all()

def test_rel_string_target():
    class Child(models.NodeModel):
        parents = models.Relationship('neo4django.Person',
                                      neo4django.Outgoing.CHILD_OF)

    assert 'child_set' in (f.name for f in Person._meta.fields)

    child = Child()
    child.parents.add(Person.objects.create(name='Han'))
    child.parents.add(Person.objects.create(name='Leia'))
    child.save()

    eq_(('Han','Leia'), tuple(sorted(p.name for p in child.parents.all())))

def test_rel_string_type():
    class Child1(models.NodeModel):
        parents = models.Relationship(Person, 'CHILD_OF')

    child = Child1()
    child.parents.add(Person.objects.create(name='Han'))
    child.parents.add(Person.objects.create(name='Leia'))
    child.save()

    eq_(('Han','Leia'), tuple(sorted(p.name for p in child.parents.all())))

    childs = child.node.relationships.all(['CHILD_OF'])[:]
    eq_(len(childs), 2)
    #test proper direction
    for r in childs:
        eq_(r.start, child.node)

def test_relationship_none():
    class Poll(models.NodeModel):
        question = models.StringProperty()

    class Choice(models.NodeModel):
        poll = models.Relationship(Poll,
                                    rel_type=neo4django.Incoming.OWNS,
                                    single=True,
                                    related_name='choices')
        choice = models.StringProperty()
    
    pbest = Poll(question="Who's the best?")
    c = Choice(poll=pbest, choice='Chris')
    eq_(len(pbest.choices.none()), 0)

    pbest.save()
    c.save()

    p = list(Poll.objects.all())[0]
    eq_(len(p.choices.none()), 0)

@with_setup(None, teardown)
def test_abstract_rel_inheritance():
    """
    Test that inheriting abstract relationships doesn't throw an error. Stems
    from GitHub issue #37.
    """
    class ZenNode(models.NodeModel):
        class Meta:
            abstract = True
        rel = models.Relationship('self',rel_type='knows')

    class Pupil(ZenNode):
        pass

    p = Pupil.objects.create()
    p.rel.add(p)
    p.save()

@with_setup(None, teardown)
def test_rel_query_direction():
    """
    Confirm GitHub issue #42, querying doesn't respect rel direction.
    """
    class LetterL(models.NodeModel):
        name = models.StringProperty()

    class LetterM(models.NodeModel):
        name = models.StringProperty()
        follows = models.Relationship(LetterL, rel_type='follows')

    class LetterN(models.NodeModel):
        name = models.StringProperty()
        follows = models.Relationship(LetterM, rel_type='follows')

    el = LetterL.objects.create(name='LLL')

    m = LetterM.objects.create(name='MMM')
    m.follows.add(el)
    m.save()

    n = LetterN.objects.create(name='NNN')
    n.follows.add(m)
    n.save()

    eq_(len(list(m.follows.all())), 1)
    eq_(len(list(m.lettern_set.all())), 1)


@with_setup(None, teardown)
def test_rel_slicing():
    class Topic(models.NodeModel):
        value = models.StringProperty()

    class TOC(models.NodeModel):
        contains = models.Relationship(Topic, rel_type='follows', preserve_ordering=True)

    toc = TOC()
    for i in xrange(5):
        toc.contains.add(Topic(value=str(i)))
    toc.save()

    for i in xrange(5):
        eq_(toc.contains.all()[i].value, str(i))

    eq_([n.value for n in toc.contains.all()[0:2]], ['0','1'])
    eq_([n.value for n in toc.contains.all()[1:-1]], ['1','2', '3'])
    eq_(toc.contains.all()[-1].value, '4')
