from decorator import decorator

def transactional(func):
    """
    A decorator that currently does, well, nothing. Regardless, flag functions
    that should be transactional so that they can be dealt with in the future,
    when the Neo4j REST interface supports transactions.
    """
    func.transactional = True
    return func

@decorator
def not_supported(func, *args, **kw):
    raise TypeError("%s is not supported." % func.__name__)

def not_implemented(arg):
    """
    A decorator that throws a NotImplementedError instead of calling the supplied
    function.

    Intended use -

    @not_implemented
    def hard_work_for_the_future():
        ...

    @not_implemented('Not implemented until version 2!')
    def hard_work_for_the_future():
        ...

    The first use will raise the error with a message of "hard_work_for_the_future",
    and the second with "Not implemented until version 2!".

    Alternative, if you'd rather use this as a function

    def hard_work_for_the_future():
        ...
    hard_work_for_the_future = not_implemented(hard_work_for_the_future)

    def hard_work_for_the_future():
        ...
    hard_work_for_the_future = not_implemented("Not implemented until version 2!")(hard_work_for_the_future)

    respectively.
    """
    from decorator import decorator
    @decorator
    def not_implemented_dec(func, *args, **kwargs):
        if isinstance(arg, str):
            raise NotImplementedError(arg)
        else:
            raise NotImplementedError(func.__name__)
    if type(arg) == type(not_implemented_dec):
        return not_implemented_dec(arg)
    return not_implemented_dec

def alters_data(func):
    func.alters_data=True
    return func

@decorator
def memoized(func, *args, **kwargs):
    from operator import itemgetter
    if not hasattr(func, 'cache'):
        func.cache = {}
    key = args + tuple(sorted(kwargs.items(), key=itemgetter(0)))
    if key in func.cache:
        return func.cache[key]
    else:
        new_val = func(*args, **kwargs)
        try:
            func.cache[key] = new_val
        except TypeError:
            #uncacheable
            pass
        return new_val

def borrows_methods(target_cls, method_names):
    """
    Copy methods from a target class onto this one.

    Expected to be used as a decorator- eg

        @borrows_methods(SomeClass, ('__str__','__hash__'))
        class MyClass(object):
            pass

    Use this in situations where you'd like to use a class as a mixin, but it
    find it has a bit too much baggage.
    """
    def wrapped(cls):
        for method_name in set(method_names):
            target_method = getattr(target_cls, method_name)
            setattr(cls, method_name, target_method.im_func)
        return cls
    return wrapped
