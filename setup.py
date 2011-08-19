from setuptools import setup

setup(
    name='neo4django',
    version='0.1',
    author='Matt Luongo',
    author_email='mhluongo@gmail.com',
    description='A Django/Neo4j ORM layer based on the neo4j.py.',
    license = 'GPL',
    url = "http://packages.python.org/neo4django",
    packages=['neo4django','neo4django.models','neo4django.tests'],
    long_description=open('README.rst').read(),
    platforms=['posix'],
    install_requires=[
        'decorator>=3.3.1',
        'jexp>=0.1.0',
        'neo4jrestclient>=1.4.3',
        'Django>=1.3',
    ],
    tests_require=[
        'nose>=1.0',
        'requests>=0.4.1',
        'nose-regression>=1.0',
    ],
    classifiers=[
        'Development Status :: 2 - Pre-Alpha',
        'License :: OSI Approved :: GNU General Public License (GPL)',
        'Operating System :: POSIX',
    ],
    dependency_links = [
        'https://github.com/mhluongo/jexp/tarball/master#egg=jexp-0.1.0',
        'https://github.com/kennethreitz/requests/tarball/add6feab02d21967ca35f1572e6202b1dd11b788#egg=requests-0.4.1-py2.6-dev',
        'https://github.com/versae/neo4j-rest-client/tarball/88d54363dd86b0f5c0f3865ccf678689a19ecb2f#egg=neo4jrestclient-dev',
  ]
)
