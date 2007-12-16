# Copyright (C) 2007, Thomas Leonard
# See http://0install.net/0compile.html

import sys, os, __main__
from logging import info
from zeroinstall.zerostore import manifest, Store

from support import *

def do_include_deps(args):
	"""include-deps"""
	buildenv = BuildEnv()

	depdir = os.path.realpath('dependencies')
	ensure_dir(depdir)

	dirs_to_copy = []

	for needed_iface in buildenv.interfaces:
		impl = buildenv.chosen_impl(needed_iface)
		assert impl
		if impl.id.startswith('/'):
			raise SafeException("Can't export '%s' as it's a local implementation (not supported yet; sorry)" % impl)
		dirs_to_copy.append(lookup(impl.id))
	
	copied = 0
	for cached in dirs_to_copy:
		required_digest = os.path.basename(cached)
		target_impl_dir = os.path.join(depdir, required_digest)
		if not os.path.isdir(target_impl_dir):
			if required_digest.startswith('sha1='):
				shutil.copytree(cached, target_impl_dir)
			else:
				manifest_data = file(os.path.join(cached, '.manifest')).read()
				manifest.copy_tree_with_verify(cached, depdir, manifest_data, required_digest)
			copied += 1

	print "Copied %d dependencies to %s (%d already there)" % (copied, depdir, len(dirs_to_copy) - copied)

__main__.commands.append(do_include_deps)