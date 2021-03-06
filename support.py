# Copyright (C) 2006, Thomas Leonard
# See http://0install.net/0compile.html

import os, sys, shutil
import subprocess
from os.path import join
from logging import info
import ConfigParser

from zeroinstall.injector import model, selections, qdom, arch
from zeroinstall.injector.arch import canonicalize_os, canonicalize_machine

from zeroinstall.injector.iface_cache import iface_cache
from zeroinstall import SafeException
from zeroinstall.zerostore import Store, NotStored
from zeroinstall.support import find_in_path

Prefixes = qdom.Prefixes

def _(x): return x


# This is An os.uname() substitute that uses as much of ZI's
# arch._uname as is available and yet has all four elements one
# normally expects from os.uname() on Posix (on Windows, arch._uname
# has only two elements).
import platform
uname = arch._uname + platform.uname()[len(arch._uname):]

ENV_FILE = '0compile.properties'
XMLNS_0COMPILE = 'http://zero-install.sourceforge.net/2006/namespaces/0compile'

install_path = os.environ.get("0COMPILE_0INSTALL", None)
if install_path is None:
	zeroinstall_dir = os.environ.get('0COMPILE_ZEROINSTALL', None)
	if zeroinstall_dir:
			# We've been run by an old version of 0install.
			# We assuming that, if installed through 0install, 0launch requires
			# the same version of Python as 0compile.
			install_prog = [sys.executable, os.path.join(zeroinstall_dir, '0install')]
			if not os.path.exists(install_prog[1]):
				# For the Windows version...
				install_prog[1] = os.path.join(zeroinstall_dir, 'zeroinstall', 'scripts', 'install.py')
	else:
		install_prog = ['0install']
else:
	install_prog = [install_path]

if os.path.isdir('dependencies'):
	dep_dir = os.path.realpath('dependencies')
	iface_cache.stores.stores.append(Store(dep_dir))
	install_prog.append('--with-store='+ dep_dir)

class NoImpl:
	id = "none"
	version = "none"
no_impl = NoImpl()

def is_package_impl(impl):
	return impl.id.startswith("package:")

def lookup(impl_or_sel):
	id = impl_or_sel.id
	if id.startswith('package:'):
		return None
	local_path = impl_or_sel.local_path
	if local_path is not None:
		if os.path.isdir(local_path):
			return local_path
		raise SafeException("Directory '%s' no longer exists. Try '0compile setup'" % local_path)
	try:
		return iface_cache.stores.lookup_any(impl_or_sel.digests)
	except NotStored, ex:
		raise NotStored(str(ex) + "\nHint: try '0compile setup'")

def ensure_dir(d, clean = False):
	if os.path.isdir(d):
		if clean:
			print "Removing", d
			shutil.rmtree(d)
		else:
			return
	if os.path.exists(d):
		raise SafeException("'%s' exists, but is not a directory!" % d)
	os.mkdir(d)

def spawn_and_check(prog, args):
	status = os.spawnv(os.P_WAIT, prog, [prog] + args)
	if status > 0:
		raise SafeException("Program '%s' failed with exit code %d" % (prog, status))
	elif status < 0:
		raise SafeException("Program '%s' failed with signal %d" % (prog, -status))

def spawn_and_check_maybe_sandboxed(readable, writable, tmpdir, prog, args):
	child = spawn_maybe_sandboxed(readable, writable, tmpdir, prog, args)
	status = child.wait()
	if status > 0:
		raise SafeException('Command failed with exit status %d' %  status)
	elif status < 0:
		raise SafeException('Command failed with signal %d' % -status)

def spawn_maybe_sandboxed(readable, writable, tmpdir, prog, args):
	"""spawn prog, with (only) the 'writable' directories writable if sandboxing is available.
	The readable directories will be readable, as well as various standard locations.
	If no sandbox is available, run without a sandbox."""

	USE_PLASH = 'USE_PLASH_0COMPILE'

	assert os.path.isabs(prog)
	_pola_run = find_in_path('pola-run')

	if _pola_run is None:
		#print "Not using sandbox (plash not installed)"
		use_plash = False
	else:
		use_plash = os.environ.get(USE_PLASH, '').lower() or 'not set'
		if use_plash in ('not set', 'false'):
			print "Not using plash: $%s is %s" % (USE_PLASH, use_plash)
			use_plash = False
		elif use_plash == 'true':
			use_plash = True
		else:
			raise Exception('$%s must be "true" or "false", not "%s"' % (USE_PLASH, use_plash))

	if not use_plash:
		return subprocess.Popen([prog] + args)
	
	print "Using plash to sandbox the build..."
	
	# We have pola-shell :-)
	pola_args = ['--prog', prog, '-B']
	for a in args:
		pola_args += ['-a', a]
	for r in readable:
		pola_args += ['-f', r]
	for w in writable:
		pola_args += ['-fw', w]
	pola_args += ['-tw', '/tmp', tmpdir]
	os.environ['TMPDIR'] = '/tmp'
	return subprocess.Popen([_pola_run] + pola_args)

def get_arch_name():
	target_os = canonicalize_os(uname[0])
	target_machine = canonicalize_machine(uname[4])
	if target_os == 'Darwin' and target_machine == 'i386':
		# this system detection shell script comes from config.guess (20090918):
		CC = os.getenv("CC_FOR_BUILD") or os.getenv("CC") or os.getenv("HOST_CC") or "cc"
		process = subprocess.Popen("(echo '#ifdef __LP64__'; echo IS_64BIT_ARCH; echo '#endif') | " +
		                           "(CCOPTS= %s -E - 2>/dev/null) | " % CC +
		                           "grep IS_64BIT_ARCH >/dev/null", stdout=subprocess.PIPE, shell=True)
		output, error = process.communicate()
		retcode = process.poll()
		if retcode == 0:
			target_machine='x86_64'
	if target_machine in ('i585', 'i686'):
		target_machine = 'i486'	# (sensible default)
	return target_os + '-' + target_machine

class BuildEnv:
	def __init__(self, need_config = True):
		if need_config and not os.path.isfile(ENV_FILE):
			raise SafeException("Run 0compile from a directory containing a '%s' file" % ENV_FILE)

		self.config = ConfigParser.RawConfigParser()
		self.config.add_section('compile')
		self.config.set('compile', 'download-base-url', '')
		self.config.set('compile', 'version-modifier', '')
		self.config.set('compile', 'interface', '')
		self.config.set('compile', 'selections', '')
		self.config.set('compile', 'metadir', '0install')
		self.config.set('compile', 'distdir', '')

		self.config.read(ENV_FILE)

		self._selections = None

		return

	@property
	def iface_name(self):
		iface_name = os.path.basename(self.interface)
		if iface_name.endswith('.xml'):
			iface_name = iface_name[:-4]
		iface_name = iface_name.replace(' ', '-')
		if iface_name.endswith('-src'):
			iface_name = iface_name[:-4]
		return iface_name

	interface = property(lambda self: model.canonical_iface_uri(self.config.get('compile', 'interface')))

	@property
	def distdir(self):
		distdir_name = self.config.get('compile', 'distdir')
		if not distdir_name:
			arch = self.target_arch.replace('*', 'any')
			distdir_name = self.iface_name.lower()
			distdir_name += '-' + arch.lower()
		assert os.path.dirname(distdir_name) == ''
		return os.path.realpath(distdir_name)

	def get_binary_template(self):
		"""Find the <compile:implementation> element for the selected compile command, if any"""
		sels = self.get_selections()
		if sels.commands:
			for elem in sels.commands[0].qdom.childNodes:
				if elem.uri == XMLNS_0COMPILE and elem.name == 'implementation':
					return elem
		return None

	@property
	def metadir(self):
		metadir = self.config.get('compile', 'metadir')
		assert not os.path.isabs(metadir)
		return join(self.distdir, metadir)

	@property
	def local_iface_file(self):
		return join(self.metadir, 'feed.xml')

	@property
	def target_arch(self):
		temp = self.get_binary_template()
		arch = temp and temp.getAttribute('arch')
		return arch or get_arch_name()

	@property
	def version_modifier(self):
		vm = self.config.get('compile', 'version-modifier')
		if vm: return vm
		if self.user_srcdir:
			return '-1'
		return ''

	@property
	def archive_stem(self):
		# Use the version that we actually built, not the version we would build now
		feed = self.load_built_feed()
		assert len(feed.implementations) == 1
		version = feed.implementations.values()[0].get_version()

		# Don't use the feed's name, as it may contain the version number
		name = feed.get_name().lower().replace(' ', '-')
		arch = self.target_arch.lower().replace('*-*', 'bin').replace('*', 'any')

		return '%s-%s-%s' % (name, arch, version)

	def load_built_feed(self):
		path = self.local_iface_file
		stream = file(path)
		try:
			feed = model.ZeroInstallFeed(qdom.parse(stream), local_path = path)
		finally:
			stream.close()
		return feed

	def load_built_selections(self):
		path = join(self.metadir, 'build-environment.xml')
		if os.path.exists(path):
			stream = file(path)
			try:
				return selections.Selections(qdom.parse(stream))
			finally:
				stream.close()
		return None

	@property
	def download_base_url(self):
		return self.config.get('compile', 'download-base-url')
	
	def chosen_impl(self, uri):
		sels = self.get_selections()
		assert uri in sels.selections
		return sels.selections[uri]

	@property
	def local_download_iface(self):
		impl, = self.load_built_feed().implementations.values()
		return '%s-%s.xml' % (self.iface_name, impl.get_version())

	def save(self):
		stream = file(ENV_FILE, 'w')
		try:
			self.config.write(stream)
		finally:
			stream.close()

	def get_selections(self, prompt = False):
		if self._selections:
			assert not prompt
			return self._selections

		selections_file = self.config.get('compile', 'selections')
		if selections_file:
			if prompt:
				raise SafeException("Selections are fixed by %s" % selections_file)
			stream = file(selections_file)
			try:
				self._selections = selections.Selections(qdom.parse(stream))
			finally:
				stream.close()
			from zeroinstall.injector import handler
			from zeroinstall.injector.config import load_config
			if os.isatty(1):
				h = handler.ConsoleHandler()
			else:
				h = handler.Handler()
			config = load_config(h)
			blocker = self._selections.download_missing(config)
			if blocker:
				print "Waiting for selected implementations to be downloaded..."
				h.wait_for_blocker(blocker)
		else:
			command = install_prog + ['download', '--source', '--xml']
			if prompt and '--console' not in install_prog:
				if os.name == 'nt':
					command[0] += '-win'
				command.append('--gui')
			command.append(self.interface)
			child = subprocess.Popen(command, stdout = subprocess.PIPE)
			try:
				self._selections = selections.Selections(qdom.parse(child.stdout))
			finally:
				if child.wait():
					raise SafeException(' '.join(repr(x) for x in command) + " failed (exit code %d)" % child.returncode)

		self.root_impl = self._selections.selections[self.interface]

		self.orig_srcdir = os.path.realpath(lookup(self.root_impl))
		self.user_srcdir = None

		if os.path.isdir('src'):
			self.user_srcdir = os.path.realpath('src')
			if self.user_srcdir == self.orig_srcdir or \
			   self.user_srcdir.startswith(os.path.join(self.orig_srcdir, '')) or \
			   self.orig_srcdir.startswith(os.path.join(self.user_srcdir, '')):
				info("Ignoring 'src' directory because it coincides with %s",
					self.orig_srcdir)
				self.user_srcdir = None

		return self._selections

	def get_build_changes(self):
		sels = self.get_selections()
		old_sels = self.load_built_selections()
		changes = []
		if old_sels:
			# See if things have changed since the last build
			all_ifaces = set(sels.selections) | set(old_sels.selections)
			for x in all_ifaces:
				old_impl = old_sels.selections.get(x, no_impl)
				new_impl = sels.selections.get(x, no_impl)
				if old_impl.version != new_impl.version:
					changes.append("Version change for %s: %s -> %s" % (x, old_impl.version, new_impl.version))
				elif old_impl.id != new_impl.id:
					changes.append("Version change for %s: %s -> %s" % (x, old_impl.id, new_impl.id))
		return changes

def depth(node):
	root = node.ownerDocument.documentElement
	depth = 0
	while node and node is not root:
		node = node.parentNode
		depth += 1
	return depth

def parse_bool(s):
	if s == 'true': return True
	if s == 'false': return False
	raise SafeException('Expected "true" or "false" but got "%s"' % s)
