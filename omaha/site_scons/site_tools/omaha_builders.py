#!/usr/bin/python2.4
#
# Copyright 2009 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ========================================================================

"""Omaha builders tool for SCons."""

from copy import copy
from copy import deepcopy
import os.path
import SCons.Action
import SCons.Builder
import SCons.Tool
from subprocess import PIPE,Popen

import omaha_version_utils

def SignDotNetManifest(env, target, unsigned_manifest):
  """Signs a .NET manifest.

  Args:
    env: The environment.
    target: Name of signed manifest.
    unsigned_manifest: Unsigned manifest.

  Returns:
    Output node list from env.Command().
  """
  mage_sign_path = ('python $MAIN_DIR/tools/retry.py 10 15 %s/%s' %
                    (os.getenv('OMAHA_NETFX_TOOLS_DIR'), 'mage.exe -Sign'))
  sign_manifest_cmd = (mage_sign_path +
                       ' $SOURCE -ToFile $TARGET -TimestampUri ' +
                       '$TIMESTAMP_SERVER ')

  if env.Bit('build_server'):
    # If signing fails with the following error, the hash may not match any
    # certificates: "Internal error, please try again. Object reference not set
    # to an instance of an object."
    sign_manifest_cmd += ('-CertHash ' + env['CERTIFICATE_HASH'])
  else:
    sign_manifest_cmd += '-CertFile %s -Password %s' % (
        env.GetOption('authenticode_file'),
        env.GetOption('authenticode_password'))

  signed_manifest = env.Command(
      target=target,
      source=unsigned_manifest,
      action=sign_manifest_cmd
  )

  return signed_manifest


def OmahaCertificateTagExe(env, target, source):
  """Adds a superfluous certificate with a magic signature to an EXE. The file
  must be signed with Authenticode in order for Certificate Tagging to succeed.

  Args:
    env: The environment.
    target: Name of the certificate-tagged file.
    source: Name of the file to be certificate-tagged.

  Returns:
    Output node list from env.Command().
  """

  certificate_tag = ('"' + env['ENV']['GOROOT'] + '/bin/go.exe' + '"' +
      ' run $MAIN_DIR/../common/certificate_tag/certificate_tag.go')
  magic_bytes = 'Gact2.0Omaha'
  padded_length = len(magic_bytes) + 2 + 8192
  certificate_tag_cmd = env.Command(
      target=target,
      source=source,
      action=certificate_tag +
             ' -set-superfluous-cert-tag=' + magic_bytes +
             ' -padded-length=' + str(padded_length) + ' -out $TARGET $SOURCE',
  )

  return certificate_tag_cmd

def OmahaTagExe(env, target, source, tag):
  """Tags an EXE using ApplyTag.

  Args:
    env: The environment.
    target: Name of the tagged file.
    source: Name of the file to be tagged.
    tag: Tag to be applied.

  Returns:
    Output node list from env.Command().
  """

  tag_exe = '$MAIN_DIR/internal/tools/ApplyTag.exe'
  tag_cmd = env.Command(
      target=target,
      source=source,
      action=tag_exe + ' $SOURCES $TARGET ' +
      '%s append' % tag,
  )

  return tag_cmd

#
# Custom Library and Program builders.
#

def ComponentStaticLibrary(env, lib_name, *args, **kwargs):
  """Pseudo-builder for static library.

  Args:
    env: Environment in which we were called.
    lib_name: Static library name.
    args: Positional arguments.
    kwargs: Keyword arguments.

  Returns:
    Output node list from env.ComponentLibrary().
  """
  return env.ComponentLibrary(lib_name, *args, **kwargs)

#
# Unit Test Builders
#

def IsBuildingModule(env, module_name):
  """Returns true if the module will be built with current build process.

  Args:
    env: The environment.
    module_name: module name.

  Returns:
    Whether the given module will be built.
  """
  return module_name in env['BUILD_SCONSCRIPTS']


def GetAllInOneUnittestSources(env):
  """Returns a list of source files for the all-in-one unit test.

  Args:
    env: The environment.

  Returns:
    A list of sources for the all-in-one unit test.
  """
  return env['all_in_one_unittest_sources']


def GetAllInOneUnittestLibs(env):
  """Returns a list of libs to be linked into the all-in-one unit test.

  Args:
    env: The environment.

  Returns:
    A list of libs for the all-in-one unit test.
  """
  # Sort to prevent spurious rebuilds caused by indeterminate ordering of a set.
  return sorted(env['all_in_one_unittest_libs'],
                key=SCons.Node.FS.Base.get_abspath)


# If a .idl file does not result in any generated proxy code (no foo_p.c and
# no foo_data.c), the default TypeLibrary builder will mistakenly believe that
# the IDL needs to be run through midl.exe again to rebuild the missing files.
def _MidlEmitter(target, source, env):
  def IsNonProxyGeneratedFile(x):
    """Returns true if x is not generated proxy code, false otherwise."""
    return not (str(x).endswith('_p.c') or str(x).endswith('_data.c'))

  (t, source) = SCons.Tool.midl.midl_emitter(target, source, env)
  return (filter(IsNonProxyGeneratedFile, t), source)


def IsCoverageBuild(env):
  """Returns true if this is a coverage build.

  Args:
    env: The environment.

  Returns:
    whether this is a coverage build.
  """
  return 'coverage' in env.subst('$BUILD_TYPE')


def CopyFileToDirectory(env, target, source):
  """Copies the file to the directory using the DOS copy command.

  In general, Replicate() should be used, but there are specific cases where
  an explicit copy is required.

  Args:
    env: The environment.
    target: The target directory.
    source: The full path to the source file.

  Returns:
    Output node list from env.Command().
  """
  (_, source_filename) = os.path.split(source)
  return env.Command(target=os.path.join(target, source_filename),
                     source=source,
                     action='@copy /y $SOURCE $TARGET')


def ConfigureEnvFor64Bit(env):
  """Modifies the flags and compiler\library paths of an environment to
     configure it to produce 64-bit binaries.

  Args:
    env: The environment.
  """
  env.AppendUnique(ARFLAGS=['/MACHINE:x64'],
                   LIBFLAGS=['/MACHINE:x64'],
                   LINKFLAGS=['/MACHINE:x64'])

  platform_sdk_lib_dir = ('$WINDOWS_SDK_10_0_DIR/lib/' +
      '$WINDOWS_SDK_10_0_VERSION')

  lib_paths = {
      omaha_version_utils.VC80: [ '$VC80_DIR/vc/lib/amd64',
                                  '$ATLMFC_VC80_DIR/lib/amd64',
                                  '$PLATFORM_SDK_VISTA_6_0_DIR/lib/x64' ],
      omaha_version_utils.VC100: [ '$VC10_0_DIR/vc/lib/amd64',
                                   '$ATLMFC_VC10_0_DIR/lib/amd64',
                                   '$PLATFORM_SDK_VC10_0_DIR/lib/x64' ],
      omaha_version_utils.VC120: [ '$VC12_0_DIR/vc/lib/amd64',
                                   '$ATLMFC_VC12_0_DIR/lib/amd64',
                                   '$WINDOWS_SDK_8_1_DIR/lib/winv6.3/um/x64' ],
      omaha_version_utils.VC140: [ '$VC14_0_DIR/vc/lib/amd64',
                                   '$ATLMFC_VC14_0_DIR/lib/amd64',
                                   platform_sdk_lib_dir + '/um/x64',
                                   platform_sdk_lib_dir + '/ucrt/x64',],
      omaha_version_utils.VC150: [ '$VC15_0_DIR/lib/x64',
                                   '$ATLMFC_VC15_0_DIR/lib/x64',
                                   '$WINDOWS_SDK_10_0_LIB_DIR/um/x64',
                                   '$WINDOWS_SDK_10_0_LIB_DIR/ucrt/x64',],
      omaha_version_utils.VC160: [ '$VC16_0_DIR/lib/x64',
                                   '$ATLMFC_VC16_0_DIR/lib/x64',
                                   '$WINDOWS_SDK_10_0_LIB_DIR/um/x64',
                                   '$WINDOWS_SDK_10_0_LIB_DIR/ucrt/x64',],
      }[env['msc_ver']]

  env.Prepend(LIBPATH=lib_paths)

  # Override the build tools to be the x86-64 version.
  env.PrependENVPath('PATH', env.Dir(
      {omaha_version_utils.VC80  : '$VC80_DIR/vc/bin/x86_amd64',
       omaha_version_utils.VC100 : '$VC10_0_DIR/vc/bin/x86_amd64',
       omaha_version_utils.VC120 : '$VC12_0_DIR/vc/bin/x86_amd64',
       omaha_version_utils.VC140 : '$VC14_0_DIR/vc/bin/x86_amd64',
       omaha_version_utils.VC150 : '$VC15_0_DIR/bin/HostX64/x64',
       omaha_version_utils.VC160 : '$VC16_0_DIR/bin/HostX64/x64'}
      [env['msc_ver']]))

  # Filter x86 options that are not supported or conflict with x86-x64 options.
  env.FilterOut(ARFLAGS=['/MACHINE:X86'],
                CCFLAGS=['/arch:IA32'],
                LIBFLAGS=['/MACHINE:X86'],
                LINKFLAGS=['/MACHINE:X86'])

  # x86-64 does not support SAFESEH option at link time.
  env.FilterOut(LINKFLAGS=['/SAFESEH'])

  # x86-64 has a different minimum requirements for the Windows subsystem.
  env.FilterOut(LINKFLAGS=['/SUBSYSTEM:WINDOWS,5.01'])
  env.AppendUnique(LINKFLAGS=['/SUBSYSTEM:WINDOWS,5.02'])

  # Modify output filenames such that .obj becomes .obj64.  (We can't modify
  # LIBPREFIX in the same way, unfortunately, because the 64-bit compilers
  # supply the base libraries as .lib.)
  env['OBJSUFFIX'] = '.obj64'

  # Set the bit to denote that this environment generates 64-bit targets.
  # (This is used by several .scons files to adjust target names.)
  env.SetBits('x64')

  # If this is a coverage build, skip instrumentation for 64-bit binaries,
  # as VSInstr doesn't currently support those.
  if env.IsCoverageBuild():
    env['INSTALL'] = env['PRECOVERAGE_INSTALL']

def CloneAndMake64Bit(env):
  """Clones the supplied environment and calls ConfigureEnvFor64Bit()
     on the clone.

  Args:
    env: The environment to clone.

  Returns:
    The cloned and modified environment.
  """
  env64 = env.Clone()
  ConfigureEnvFor64Bit(env64)
  return env64


def GetMultiarchLibName(env, lib_name):
  """Decorates the lib name based on whether or not the environment is intended
  to produce 64-bit binaries.

  Args:
    env: The environment to build in.
    lib_name: The library name.

  Returns:
    The appropriate library name.
  """
  filename = (lib_name, '%s_64' % lib_name)[env.Bit('x64')]
  return '$LIB_DIR/' + filename + '.lib'


def ComponentStaticLibraryMultiarch(env, lib_name, *args, **kwargs):
  """Calls ComponentStaticLibrary() twice - once with the supplied environment,
  and once with a 64-bit leaf of that environment.

  Args:
    env: The environment.
    lib_name: The name of the library to be built.
    args: Positional arguments.
    kwargs: Keyword arguments.

  Returns:
    The output node lists from env.ComponentLibrary().
  """

  # ComponentStaticLibrary() will actually modify the input arg lists, so
  # make a copy of both. It's only necessary to make a shallow copy of each arg
  # in |args|, as the modification by ComponentStaticLibrary only adds to one
  # of the argument lists.
  args64 = [copy(arg) for arg in args]
  kwargs64 = deepcopy(kwargs)

  nodes32 = ComponentStaticLibrary(env.Clone(), lib_name, *args, **kwargs)
  nodes64 = ComponentStaticLibrary(CloneAndMake64Bit(env),
                                   '%s_64' % lib_name,
                                   *args64, **kwargs64)
  return nodes32 + nodes64


# os.path.relpath is not available in Python 2.4, so make our own.
def RelativePath(path, start):
  """Returns a relative path.

  Args:
    path: Some path.
    start: A parent of |path|.

  Returns:
    A relative path from |start| to |path|.
  """
  path_list = [x for x in os.path.normpath(path).split(os.path.sep) if x]
  start_list = [x for x in os.path.normpath(start).split(os.path.sep) if x]
  i = 0
  for start_item, path_item in zip(start_list, path_list):
    if start_item.lower() != path_item.lower():
      break
    i += 1
  rel_list = [os.path.pardir] * (len(start_list)-i) + path_list[i:]
  if not rel_list:
    return os.path.curdir
  return os.path.join(*rel_list)


def CompileProtoBuf(env, input_proto_files):
  """Invokes the protocol buffer compiler.

  Args:
    env: The environment, which must specify the following construction
        variables
        $PROTO_PATH: The "proto path" passed to protoc. This is the base path in
                     which all .proto files reside.
        $CPP_OUT: The "output path" passed to protoc. This is the path into
                  which the generated files are placed, preserving their paths
                  relative to $PROTO_PATH.
    input_proto_files: The protocol buffer .proto file(s).

  Returns:
    Output node list of generated .cc files.
  """
  proto_compiler_path = '%s/protoc.exe' % os.getenv('OMAHA_PROTOBUF_BIN_DIR',
                                                    '$OBJ_ROOT/base/Default')
  proto_path = env['PROTO_PATH']
  cpp_out = env['CPP_OUT']
  # Generate the list of .pb.cc and .pb.h targets in the cpp_out dir.
  targets = [os.path.join(cpp_out, os.path.splitext(base)[0] + ext)
             for base in [RelativePath(in_file, proto_path)
                          for in_file in input_proto_files]
             for ext in ('.pb.cc', '.pb.h')]
  proto_arguments = (' --proto_path=%s --cpp_out=%s %s ' %
                     (proto_path,
                      cpp_out,
                      ' '.join(input_proto_files)))
  proto_cmd_line = proto_compiler_path + proto_arguments
  compile_proto_buf = env.Command(
      target=targets,
      source=input_proto_files,
      action=proto_cmd_line,
  )

  if 'OMAHA_PROTOBUF_BIN_DIR' not in os.environ:
    env.Depends(compile_proto_buf, proto_compiler_path)

  # Return only the generated .pb.cc files for convenience.
  return [node for node in compile_proto_buf if node.name.endswith('.cc')]


# NOTE: SCons requires the use of this name, which fails gpylint.
def generate(env):  # pylint: disable-msg=C6409
  """SCons entry point for this tool."""
  env.AddMethod(SignDotNetManifest)
  env.AddMethod(OmahaCertificateTagExe)
  env.AddMethod(OmahaTagExe)
  env.AddMethod(IsBuildingModule)
  env.AddMethod(GetAllInOneUnittestSources)
  env.AddMethod(GetAllInOneUnittestLibs)
  env.AddMethod(IsCoverageBuild)
  env.AddMethod(CopyFileToDirectory)
  env.AddMethod(ConfigureEnvFor64Bit)
  env.AddMethod(CloneAndMake64Bit)
  env.AddMethod(GetMultiarchLibName)
  env.AddMethod(ComponentStaticLibraryMultiarch)
  env.AddMethod(CompileProtoBuf)

  env['MIDLNOPROXYCOM'] = ('$MIDL $MIDLFLAGS /tlb ${TARGETS[0]} '
                           '/h ${TARGETS[1]} /iid ${TARGETS[2]} '
                           '$SOURCE 2> NUL')
  env['BUILDERS']['TypeLibraryWithNoProxy'] = SCons.Builder.Builder(
      action=SCons.Action.Action('$MIDLNOPROXYCOM', '$MIDLNOPROXYCOMSTR'),
      src_suffix='.idl',
      suffix='.tlb',
      emitter=_MidlEmitter,
      source_scanner=SCons.Tool.midl.idl_scanner)
