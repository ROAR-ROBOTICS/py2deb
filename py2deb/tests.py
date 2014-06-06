# Automated tests for the `py2deb' package.
#
# Author: Peter Odding <peter.odding@paylogic.com>
# Last Change: June 6, 2014
# URL: https://py2deb.readthedocs.org

"""
The :py:mod:`py2deb.tests` module contains the automated tests for `py2deb`.

The makefile in the py2deb git repository uses pytest_ to run the test suite
because of pytest's great error reporting. Nevertheless the test suite is
written to be compatible with the :py:mod:`unittest` module (part of Python's
standard library) so that the test suite can be run without additional external
dependencies.

.. _pytest: http://pytest.org/latest/goodpractises.html
"""

# Standard library modules.
import fnmatch
import functools
import glob
import logging
import os
import sys
import unittest

# External dependencies.
import coloredlogs
from deb_pkg_tools.control import load_control_file
from deb_pkg_tools.package import inspect_package, parse_filename
from executor import execute

# Modules included in our package.
from py2deb.cli import main
from py2deb.converter import PackageConverter
from py2deb.utils import TemporaryDirectory

# Initialize a logger.
logger = logging.getLogger(__name__)
execute = functools.partial(execute, logger=logger)

# Find the sample packages that we're going to build during our tests.
TESTS_DIRECTORY = os.path.dirname(os.path.abspath(__file__))
TRIVIAL_PACKAGE_DIRECTORY = os.path.join(TESTS_DIRECTORY, 'samples', 'trivial-package')


class PackageConverterTestCase(unittest.TestCase):

    """
    :py:mod:`unittest` compatible container for the test suite of `py2deb`.
    """

    def setUp(self):
        """
        Initialize verbose logging to the terminal.
        """
        coloredlogs.install()
        coloredlogs.increase_verbosity()

    def test_argument_validation(self):
        """
        Test argument validation done by setters of :py:class:py2deb.converter.PackageConverter`.
        """
        converter = PackageConverter()
        self.assertRaises(ValueError, converter.set_repository, '/foo/bar/baz')
        self.assertRaises(ValueError, converter.set_name_prefix, '')
        self.assertRaises(ValueError, converter.rename_package, 'old-name', '')
        self.assertRaises(ValueError, converter.rename_package, '', 'new-name')
        self.assertRaises(ValueError, converter.set_install_prefix, '')
        self.assertRaises(ValueError, converter.install_alternative, 'link', '')
        self.assertRaises(ValueError, converter.install_alternative, '', 'path')
        self.assertRaises(ValueError, converter.set_conversion_command, 'package-name', '')
        self.assertRaises(ValueError, converter.set_conversion_command, '', 'command')

    def test_conversion_of_simple_package(self):
        """
        Convert a simple Python package without any dependencies.

        Converts coloredlogs_ and sanity checks the result. Performs several static
        checks on the metadata and contents of the resulting package archive.

        .. _coloredlogs: https://pypi.python.org/pypi/coloredlogs
        """
        # Use a temporary directory as py2deb's repository directory so that we
        # can easily find the *.deb archive generated by py2deb.
        with TemporaryDirectory() as directory:
            # Prepare a control file to be patched.
            control_file = os.path.join(directory, 'control')
            with open(control_file, 'w') as handle:
                handle.write('Depends: vim\n')
            # Run the conversion command.
            py2deb('--repository=%s' % directory,
                   '--report-dependencies=%s' % control_file,
                   'coloredlogs==0.4.8')
            # Check that the control file was patched.
            control_fields = load_control_file(control_file)
            assert control_fields['Depends'].matches('vim')
            assert control_fields['Depends'].matches('python-coloredlogs', '0.4.8')
            # Find the generated Debian package archive.
            archives = glob.glob('%s/*.deb' % directory)
            logger.debug("Found generated archive(s): %s", archives)
            assert len(archives) == 1
            # Use deb-pkg-tools to inspect the generated package.
            metadata, contents = inspect_package(archives[0])
            logger.debug("Metadata of generated package: %s", dict(metadata))
            logger.debug("Contents of generated package: %s", dict(contents))
            # Check the package metadata.
            assert metadata['Package'] == 'python-coloredlogs'
            assert metadata['Version'].startswith('0.4.8')
            assert metadata['Architecture'] == 'all'
            # There should be exactly one dependency: some version of Python.
            assert metadata['Depends'].matches('python%i.%i' % sys.version_info[:2])
            # Don't care about the format here as long as essential information is retained.
            assert 'Peter Odding' in metadata['Maintainer']
            assert 'peter@peterodding.com' in metadata['Maintainer']
            # Check the package contents.
            # Check for the two *.py files that should be installed by the package.
            assert find_file(contents, '/usr/lib/python*/dist-packages/coloredlogs/__init__.py')
            assert find_file(contents, '/usr/lib/python*/dist-packages/coloredlogs/converter.py')
            # Make sure the file ownership and permissions are sane.
            archive_entry = find_file(contents, '/usr/lib/python*/dist-packages/coloredlogs/__init__.py')
            assert archive_entry.owner == 'root'
            assert archive_entry.group == 'root'
            assert archive_entry.permissions == '-rw-r--r--'

    def test_custom_conversion_command(self):
        """
        Convert a simple Python package that requires a custom conversion command.

        Converts Fabric and sanity checks the result. For details please refer
        to :py:func:`py2deb.converter.PackageConverter.set_conversion_command()`.
        """
        with TemporaryDirectory() as directory:
            # Run the conversion command.
            converter = PackageConverter()
            converter.set_repository(directory)
            converter.set_conversion_command('Fabric', 'rm -Rf paramiko')
            converter.convert(['Fabric==0.9.0'])
            # Find the generated Debian package archive.
            archives = glob.glob('%s/*.deb' % directory)
            logger.debug("Found generated archive(s): %s", archives)
            pathname = find_package_archive(archives, 'python-fabric')
            # Use deb-pkg-tools to inspect the generated package.
            metadata, contents = inspect_package(pathname)
            # Check for the two *.py files that should be installed by the package.
            for filename, entry in contents.items():
                if filename.startswith('/usr/lib') and not entry.permissions.startswith('d'):
                    assert 'fabric' in filename.lower()
                    assert 'paramiko' not in filename.lower()

    def test_conversion_of_package_with_dependencies(self):
        """
        Convert a non trivial Python package with several dependencies.

        Converts deb-pkg-tools_ to a Debian package archive and sanity checks the
        result. Performs static checks on the metadata (dependencies) of the
        resulting package archive.

        .. _deb-pkg-tools: https://pypi.python.org/pypi/deb-pkg-tools
        """
        # Use a temporary directory as py2deb's repository directory so that we
        # can easily find the *.deb archive generated by py2deb.
        with TemporaryDirectory() as directory:
            # Run the conversion command.
            py2deb('--repository=%s' % directory, 'deb-pkg-tools==1.14.7')
            # Find the generated Debian package archives.
            archives = glob.glob('%s/*.deb' % directory)
            logger.debug("Found generated archive(s): %s", archives)
            # Make sure the expected dependencies have been converted.
            assert sorted(parse_filename(a).name for a in archives) == sorted([
                'python-chardet',
                'python-coloredlogs',
                'python-deb-pkg-tools',
                'python-debian',
                'python-executor',
                'python-humanfriendly',
            ])
            # Use deb-pkg-tools to inspect ... deb-pkg-tools :-)
            pathname = find_package_archive(archives, 'python-deb-pkg-tools')
            metadata, contents = inspect_package(pathname)
            logger.debug("Metadata of generated package: %s", dict(metadata))
            logger.debug("Contents of generated package: %s", dict(contents))
            # Make sure the dependencies defined in `stdeb.cfg' have been preserved.
            for configured_dependency in ['apt', 'apt-utils', 'binutils', 'dpkg-dev', 'fakeroot', 'gnupg', 'lintian']:
                logger.debug("Checking configured dependency %s ..", configured_dependency)
                assert metadata['Depends'].matches(configured_dependency) is not None
            # Make sure the dependencies defined in `setup.py' have been preserved.
            expected_dependencies = [
                'python-chardet', 'python-coloredlogs', 'python-debian',
                'python-executor', 'python-humanfriendly'
            ]
            for python_dependency in expected_dependencies:
                logger.debug("Checking Python dependency %s ..", python_dependency)
                assert metadata['Depends'].matches(python_dependency) is not None

    def test_conversion_of_isolated_packages(self):
        """
        Convert a group of packages with a custom name and installation prefix.

        Converts pip-accel_ and its dependencies to a group of "isolated Debian
        packages" that are installed with a custom name prefix and installation
        prefix and sanity check the result. Also tests the ``--rename=FROM,TO``
        command line option. Performs static checks on the metadata and contents of
        the resulting package archive.
        """
        # Use a temporary directory as py2deb's repository directory so that we
        # can easily find the *.deb archive generated by py2deb.
        with TemporaryDirectory() as directory:
            # Run the conversion command.
            py2deb('--repository=%s' % directory,
                   '--name-prefix=pip-accel',
                   '--install-prefix=/usr/lib/pip-accel',
                   # By default py2deb will generate a package called
                   # `pip-accel-pip-accel'. The --no-name-prefix=PKG
                   # option can be used to avoid this.
                   '--no-name-prefix=pip-accel',
                   # Strange but valid use case (renaming a dependency):
                   # pip-accel-coloredlogs -> pip-accel-coloredlogs-renamed
                   '--rename=coloredlogs,pip-accel-coloredlogs-renamed',
                   # Also test the update-alternatives integration.
                   '--install-alternative=/usr/bin/pip-accel,/usr/lib/pip-accel/bin/pip-accel',
                   'pip-accel==0.12')
            # Find the generated Debian package archives.
            archives = glob.glob('%s/*.deb' % directory)
            logger.debug("Found generated archive(s): %s", archives)
            # Make sure the expected dependencies have been converted.
            assert sorted(parse_filename(a).name for a in archives) == sorted([
                'pip-accel',
                'pip-accel-coloredlogs-renamed',
                'pip-accel-humanfriendly',
                'pip-accel-pip',
            ])
            # Use deb-pkg-tools to inspect pip-accel.
            pathname = find_package_archive(archives, 'pip-accel')
            metadata, contents = inspect_package(pathname)
            logger.debug("Metadata of generated package: %s", dict(metadata))
            logger.debug("Contents of generated package: %s", dict(contents))
            # Make sure the dependencies defined in `setup.py' have been
            # preserved while their names have been converted.
            assert metadata['Depends'].matches('pip-accel-coloredlogs-renamed', '0.4.6')
            assert metadata['Depends'].matches('pip-accel-humanfriendly', '1.6')
            assert metadata['Depends'].matches('pip-accel-pip', '1.4')
            assert not metadata['Depends'].matches('pip-accel-pip', '1.3')
            assert not metadata['Depends'].matches('pip-accel-pip', '1.5')
            # Make sure the executable script has been installed and is marked as executable.
            pip_accel_executable = find_file(contents, '/usr/lib/pip-accel/bin/pip-accel')
            assert pip_accel_executable.permissions == '-rwxr-xr-x'
            # Verify the existence of some expected files (picked more or less at random).
            assert find_file(contents, '/usr/lib/pip-accel/lib/pip_accel/__init__.py')
            assert find_file(contents, '/usr/lib/pip-accel/lib/pip_accel/deps/debian.ini')
            assert find_file(contents, '/usr/lib/pip-accel/lib/pip_accel-0.12.egg-info/PKG-INFO')
            # Verify that all files are installed in the custom installation
            # prefix. We have to ignore directories, otherwise we would start
            # complaining about the parent directories /, /usr, /usr/lib, etc.
            for filename, properties in contents.iteritems():
                is_directory = properties.permissions.startswith('d')
                in_isolated_directory = filename.startswith('/usr/lib/pip-accel/')
                assert is_directory or in_isolated_directory


def py2deb(*arguments):
    """
    Test everything including command line parsing & validation by running py2deb's main function.

    :param arguments: The command line arguments to pass to `py2deb` (one or more strings).
    """
    sys.argv[1:] = arguments
    main()


def find_package_archive(available_archives, package_name):
    """
    Find the ``*.deb`` archive of a specific package.

    :param available_packages: The pathnames of the available package archives
                               (a list of strings).
    :param package_name: The name of the package whose archive file we're
                         interested in (a string).
    :returns: The pathname of the package archive (a string).
    :raises: :py:exc:`exceptions.AssertionError` if zero or more than one
             package archive is found.
    """
    matches = []
    for pathname in available_archives:
        if parse_filename(pathname).name == package_name:
            matches.append(pathname)
    assert len(matches) == 1, "Expected to match exactly one package archive!"
    return matches[0]


def find_file(contents, pattern):
    """
    Find the file matching the given filename pattern.

    Searches the dictionary of Debian package archive entries reported by
    :py:func:`deb_pkg_tools.package.inspect_package()`.

    :param contents: The dictionary of package archive entries.
    :param pattern: The filename pattern to match (:py:mod:`fnmatch` syntax).
    :returns: The metadata of the matched file.
    :raises: :py:exc:`exceptions.AssertionError` if zero or more than one
             archive entry is found.
    """
    matches = []
    for filename, metadata in contents.iteritems():
        if fnmatch.fnmatch(filename, pattern):
            matches.append(metadata)
    assert len(matches) == 1, "Expected to match exactly one archive entry!"
    return matches[0]


# vim: ts=4 sw=4 et nowrap
