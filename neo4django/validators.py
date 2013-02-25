from django.core import exceptions

class ArrayValidator(object):
    def __call__(self, values):
        sup = super(ArrayValidator, self)
        if hasattr(sup, '__call__'):
            sup.__call__(values)
        if not getattr(values, '__iter__', False):
            raise exceptions.ValidationError('Enter a non-string sequence.')
    
validate_array = ArrayValidator()

def validate_str(value):
    try:
        str(value)
    except:
        raise exceptions.ValidationError('Enter a valid str.')

def validate_basestring(value):
    if not isinstance(value, basestring):
        raise exceptions.ValidationError('Enter a valid str.')

def validate_int(value):
    if not isinstance(value, int):
        raise exceptions.ValidationError('Enter a valid int.')

class ElementValidator(object):
    """Validates a sequence element by element with a list of validators."""
    def __init__(self, validators, message='Invalid sequence of elements.',
                 *args, **kwargs):
        """
        Arguments:
        validators -- a sequence of callable validators

        Keyword arguments:
        message -- the error message to raise if the sequence is invalid
        """
        super(ElementValidator, self).__init__(*args, **kwargs)
        self.validators = validators
        self.message = message

    def __call__(self, values):
        sup = super(ElementValidator, self)
        if hasattr(sup, '__call__'):
            sup.__call__(values)
        try:
            for value in values:
                for validator in self.validators:
                    validator(value)
        except exceptions.ValidationError:
            raise exceptions.ValidationError(self.message)

class IntArrayValidator(ArrayValidator, ElementValidator):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault('message','Enter a sequence of valid ints.')
        super(IntArrayValidator, self).__init__([validate_int],
                                                *args, **kwargs)

class StringArrayValidator(ArrayValidator, ElementValidator):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault('message','Enter a sequence of valid strs.')
        super(StringArrayValidator, self).__init__([validate_basestring],
                                                   *args, **kwargs)
       
validate_str_array = StringArrayValidator()
validate_int_array = IntArrayValidator()

