"""Tasks to help Robot Framework packaging and other development.

Executed by Invoke <http://pyinvoke.org>. Install it with `pip install invoke`
and run `invoke --help` and `invode --list` for details how to execute tasks.
"""

import os
import os.path
import re
import shutil
import time
import urllib
import zipfile

from invoke import task, run


assert os.getcwd() == os.path.dirname(__file__)

VERSION_RE = re.compile('^((2\.\d+)(\.\d+)?)((a|b|rc|.dev)\d+)?$')
VERSION_FILE = os.path.join('src', 'robot', 'version.py')


@task(default=True)
def help():
    """Show help, basically an alias for --help.

    This task can be removed once the fix to this issue is released:
    https://github.com/pyinvoke/invoke/issues/180
    """
    run('invoke --help')


@task
def tag_release(version):
    """Tag specified release.

    Updates version using `set_version`, creates tag, and pushes changes.
    """
    version = set_version(version, push=True)
    run("git tag -a {0} -m 'Release {0}'".format(version))
    run("git push --tags")


@task
def set_version(version, push=False):
    """Set version in `src/robot/version.py`.

    Version can have these values:
    - Actual version number to use. See below for supported formats.
    - String 'dev' to update version to latest development version
      (e.g. 2.8 -> 2.8.1.dev, 2.8.1 -> 2.8.2.dev, 2.8a1 -> 2.8.dev) with
      the current date added or updated.
    - String 'keep' to keep using the previously set version.

    Given version must be in one of these PEP-440 compatible formats:
    - Stable version in 'X.Y' or 'X.Y.Z' format (e.g. 2.8, 2.8.6)
    - Pre-releases with 'aN', 'bN' or 'rcN' postfix (e.g. 2.8a1, 2.8.6rc2)
    - Development releases with '.devYYYYMMDD' postfix (e.g. 2.8.6.dev20141001)
      or with '.dev' alone (e.g. 2.8.6.dev) in which case date is added
      automatically.

    Args:
        version:  Version to use. See above for supported values and formats.
        push:     Commit and push changes to the remote repository.
    """
    if version and version != 'keep':
        version = validate_version(version)
        write_version_file(version)
        write_pom_file(version)
    version = get_version_from_file()
    print 'Version:', version
    if push:
        git_commit([VERSION_FILE, 'pom.xml'],
                   'Updated version to {}'.format(version), push=True)
    return version

def validate_version(version):
    if version == 'dev':
        version = get_dev_version()
    if version.endswith('.dev'):
        version += time.strftime('%Y%m%d')
    if not VERSION_RE.match(version):
        raise ValueError("Invalid version '{}'.".format(version))
    return version

def get_dev_version():
    previous = get_version_from_file()
    major, minor, pre = VERSION_RE.match(previous).groups()[1:4]
    if not pre:
        minor = '.{}'.format(int(minor[1:]) + 1 if minor else 1)
    return '{}{}.dev'.format(major, minor)

def write_version_file(version):
    update_file(VERSION_FILE, "VERSION = '.*'", version)

def write_pom_file(version):
    update_file('pom.xml', '<version>.*</version>', version)

def update_file(path, pattern, replacement):
    replacement = pattern.replace('.*', replacement )
    with open(path) as version_file:
        content = ''.join(re.sub(pattern, replacement, line)
                          for line in version_file)
    with open(path, 'w') as version_file:
        version_file.write(content)

def get_version_from_file():
    namespace = {}
    execfile(VERSION_FILE, namespace)
    return namespace['get_version']()

def git_commit(paths, message, push=False):
    paths = paths if isinstance(paths, basestring) else ' '.join(paths)
    run("git commit -m '{}' {}".format(message, paths))
    if push:
        run('git push')


@task
def clean(remove_dist=True, create_dirs=False):
    """Clean workspace.

    By default deletes 'build' and 'dist' directories and removes '*.pyc'
    and '$py.class' files.

    Args:
        remove_dist:  Remove also 'dist' (default).
        create_dirs:  Re-create 'build' and 'dist' after removing them.
    """
    directories = ['build', 'dist']
    for name in directories:
        if os.path.isdir(name) and (name != 'dist' or remove_dist):
            shutil.rmtree(name)
        if create_dirs and not os.path.isdir(name):
            os.mkdir(name)
    for directory, _, files in os.walk('.'):
        for name in files:
            if name.endswith(('.pyc', '$py.class')):
                os.remove(os.path.join(directory, name))


@task
def sdist(deploy=False, remove_dist=False):
    """Create source distribution.

    Args:
        deploy:       Register and upload sdist to PyPI.
        remove_dist:  Control is 'dist' directory initially removed or not.
    """
    clean(remove_dist, create_dirs=True)
    run('python setup.py sdist --force-manifest'
        + (' register upload' if deploy else ''))
    announce()

def announce():
    print
    print 'Distributions:'
    for name in os.listdir('dist'):
        print os.path.join('dist', name)


@task
def wininst(remove_dist=False):
    """Create Windows installer.

    Args:
        remove_dist:  Control is 'dist' directory initially removed or not.
    """
    clean(remove_dist, create_dirs=True)
    run('python setup.py bdist_wininst '
        '--bitmap robot.bmp --install-script robot_postinstall.py')
    announce()


@task
def jar(jython_version='2.5.3', remove_dist=False):
    """Create JAR distribution.

    Downloads Jython JAR if needed.

    Args:
        remove_dist:  Control is 'dist' directory initially removed or not.
        jython_version: Jython version to use as a base. Must match version in
            `jython-standalone-<version>.jar` found from Maven central.
            Currently `2.5.3` by default.
    """
    clean(remove_dist, create_dirs=True)
    jython_jar = get_jython_jar(jython_version)
    print 'Using {}'.format(jython_jar)
    compile_java_files(jython_jar)
    unzip_jar(jython_jar)
    copy_robot_files()
    compile_python_files(jython_jar)
    create_robot_jar(get_version_from_file())
    announce()

def get_jython_jar(version):
    lib = 'ext-lib'
    jar = os.path.join(lib, 'jython-standalone-{}.jar'.format(version))
    if os.path.exists(jar):
        return jar
    url = ('http://search.maven.org/remotecontent?filepath=org/python/'
           'jython-standalone/{0}/jython-standalone-{0}.jar').format(version)
    print 'Jython not found, downloading it from {}.'.format(url)
    if not os.path.exists(lib):
        os.mkdir(lib)
    urllib.urlretrieve(url, jar)
    return jar

def compile_java_files(jython_jar, build_dir='build'):
    root = os.path.join('src', 'java', 'org', 'robotframework')
    files = [os.path.join(root, name) for name in os.listdir(root)
             if name.endswith('.java')]
    print 'Compiling {} Java files.'.format(len(files))
    run('javac -d {target} -target 1.5 -source 1.5 -cp {cp} {files}'.format(
        target=build_dir, cp=jython_jar, files=' '.join(files)))

def unzip_jar(path, target='build'):
    zipfile.ZipFile(path).extractall(target)

def copy_robot_files(build_dir='build'):
    source = os.path.join('src', 'robot')
    target = os.path.join(build_dir, 'Lib', 'robot')
    shutil.copytree(source, target, ignore=shutil.ignore_patterns('*.pyc'))
    shutil.rmtree(os.path.join(target, 'htmldata', 'testdata'))

def compile_python_files(jython_jar, build_dir='build'):
    run('java -jar {} -m compileall {}'.format(jython_jar, build_dir))
    # Jython will not work without its py-files, but robot will
    for directory, _, files in os.walk(os.path.join(build_dir, 'Lib', 'robot')):
        for name in files:
            if name.endswith('.py'):
                os.remove(os.path.join(directory, name))

def create_robot_jar(version, source='build'):
    write_manifest(version, source)
    target = os.path.join('dist', 'robotframework-{}.jar'.format(version))
    run('jar cvfM {} -C {} .'.format(target, source))

def write_manifest(version, build_dir='build'):
    with open(os.path.join(build_dir, 'META-INF', 'MANIFEST.MF'), 'w') as mf:
        mf.write('''\
Manifest-Version: 1.0
Main-Class: org.robotframework.RobotFramework
Specification-Version: 2
Implementation-Version: {version}
'''.format(version=version))

@task
def get_issues(version=get_version_from_file(), login=None, password=None):
    """Get issues from Github.

    Requires for you to have PyGithub installed.
    https://github.com/jacquev6/PyGithub
    pip install PyGithub

    Args:
        version:  Version to get the issues for. By default the current version.
        login:    Github login. If not given anonymous login is used. There is 60
                  request maximum/hour at github api if you dont authenticate.
        password: The password for github login
    """
    try:
        from github import Github
    except ImportError:
        print "You need to install PyGithub:\npip install PyGithub\n\n"
    repo = Github(login_or_token=login, password=password).get_repo("robotframework/robotframework")
    issues = sorted([Issue(issue) for issue in repo.get_issues(milestone=_get_milestone(repo, version), state="all")])
    print "ID  | Type | Priority | Summary"
    print "--- | ---- | -------- | -------"
    for issue in issues:
        print " #{} | {} | {} | {} ".format(issue.number, issue.type, issue.prio, issue.summary )
    print "Altogether {} issues.".format(len(issues))
    _print_bwic(issues)

def _get_milestone(repo, milestone):
    for m in repo.get_milestones():
        if m.title == milestone:
            return m
    raise AssertionError("Milestone {} not found from repository {}!".format(milestone, repo.name))

def _print_bwic(issues):
    bwics = [issue for issue in issues if issue.bwic]
    if bwics:
        print "\n\nBackwards incompatible issues"
        for issue in bwics:
            print "#{} {}".format(issue.number, issue.summary)


class Issue(object):

    PRIORITIES= ["prio-critical", "prio-high", "prio-medium", "prio-low"]

    def __init__(self, issue):
        self.number = issue.number
        self.labels = [label.name for label in issue.get_labels()]
        self.type = self._get_label("bug", "enhancement")
        self._priority = self._get_label
        self.sort_table = [self.PRIORITIES.index(self._priority), 0 if self.type=="bug" else 1, self.number]
        self.summary = issue.title
        self.bwic = self._get_label("bwic")

    @property
    def prio(self):
        return self._priority.split('-')[1]

    def _get_label(self, *values):
        for value in values:
            if value in self.labels:
                return value
        return None

    def __cmp__(self, other):
        return cmp(self.sort_table, other.sort_table)