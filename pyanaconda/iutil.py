#
# iutil.py - generic install utility functions
#
# Copyright (C) 1999, 2000, 2001, 2002, 2003, 2004, 2005, 2006, 2007
# Red Hat, Inc.  All rights reserved.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
# Author(s): Erik Troan <ewt@redhat.com>
#

import glob
import os, string, stat, sys
import shutil
import signal
import os.path
import errno
import subprocess
import threading
import re

from flags import flags
from constants import *

import gettext
_ = lambda x: gettext.ldgettext("anaconda", x)

import logging
log = logging.getLogger("anaconda")
program_log = logging.getLogger("program")

from anaconda_log import program_log_lock

def augmentEnv():
    env = os.environ.copy()
    env.update({"LC_ALL": "C",
                "ANA_INSTALL_PATH": ROOT_PATH
               })
    return env

def _run_program(argv, root='/', stdin=None, env_prune=None):
    if env_prune is None:
        env_prune = []

    def chroot():
        if root and root != '/':
            os.chroot(root)

    with program_log_lock:
        program_log.info("Running... %s" % " ".join(argv))

        env = augmentEnv()
        for var in env_prune:
            env.pop(var, None)

        try:
            proc = subprocess.Popen(argv,
                                    stdin=stdin,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT,
                                    preexec_fn=chroot, cwd=root, env=env)

            out = proc.communicate()[0]
            if out:
                for line in out.splitlines():
                    program_log.info(line)

        except OSError as e:
            program_log.error("Error running %s: %s" % (argv[0], e.strerror))
            raise

        program_log.debug("Return code: %d" % proc.returncode)

    return (proc.returncode, out)

## Run an external program and redirect the output to a file.
# @param command The command to run.
# @param argv A list of arguments.
# @param stdin The file descriptor to read stdin from.
# @param stdout The file descriptor to redirect stdout to.
# @param stderr The file descriptor to redirect stderr to.
# @param root The directory to chroot to before running command.
# @return The return code of command.
def execWithRedirect(command, argv, stdin = None, stdout = None,
                     stderr = None, root = '/', env_prune=[]):
    if flags.testing:
        log.info("not running command because we're testing: %s %s"
                   % (command, " ".join(argv)))
        return 0

    argv = [command] + argv
    return _run_program(argv, stdin=stdin, root=root, env_prune=env_prune)[0]

## Run an external program and capture standard out.
# @param command The command to run.
# @param argv A list of arguments.
# @param stdin The file descriptor to read stdin from.
# @param stderr The file descriptor to redirect stderr to.
# @param root The directory to chroot to before running command.
# @param fatal Boolean to determine if non-zero exit is fatal.
# @return The output of command from stdout.
def execWithCapture(command, argv, stdin = None, stderr = None, root='/',
                    fatal = False):
    if flags.testing:
        log.info("not running command because we're testing: %s %s"
                    % (command, " ".join(argv)))
        return ""

    argv = [command] + argv
    return _run_program(argv, stdin=stdin, root=root)[1]

## Run a shell.
def execConsole():
    try:
        proc = subprocess.Popen(["/bin/sh"])
        proc.wait()
    except OSError as e:
        raise RuntimeError, "Error running /bin/sh: " + e.strerror

def getDirSize(dir):
    """ Get the size of a directory and all its subdirectories.
    @param dir The name of the directory to find the size of.
    @return The size of the directory in kilobytes.
    """
    def getSubdirSize(dir):
        # returns size in bytes
        try:
            mydev = os.lstat(dir)[stat.ST_DEV]
        except OSError as e:
            log.debug("failed to stat %s: %s" % (dir, e))
            return 0

        try:
            dirlist = os.listdir(dir)
        except OSError as e:
            log.debug("failed to listdir %s: %s" % (dir, e))
            return 0

        dsize = 0
        for f in dirlist:
            curpath = '%s/%s' % (dir, f)
            try:
                sinfo = os.lstat(curpath)
            except OSError as e:
                log.debug("failed to stat %s/%s: %s" % (dir, f, e))
                continue

            if stat.S_ISDIR(sinfo[stat.ST_MODE]):
                if os.path.ismount(curpath):
                    continue
                if mydev == sinfo[stat.ST_DEV]:
                    dsize += getSubdirSize(curpath)
            elif stat.S_ISREG(sinfo[stat.ST_MODE]):
                dsize += sinfo[stat.ST_SIZE]

        return dsize
    return getSubdirSize(dir)/1024

## Create a directory path.  Don't fail if the directory already exists.
# @param dir The directory path to create.
def mkdirChain(dir):
    try:
        os.makedirs(dir, 0755)
    except OSError as e:
        try:
            if e.errno == errno.EEXIST and stat.S_ISDIR(os.stat(dir).st_mode):
                return
        except OSError:
            pass

        log.error("could not create directory %s: %s" % (dir, e.strerror))

def isConsoleOnVirtualTerminal():
    # XXX PJFIX is there some way to ask the kernel this instead?
    # XXX we don't want to have to import storage from here
    if os.uname()[4].startswith("s390"):
        return False
    return not flags.serial

def strip_markup(text):
    if text.find("<") == -1:
        return text
    r = ""
    inTag = False
    for c in text:
        if c == ">" and inTag:
            inTag = False
            continue
        elif c == "<" and not inTag:
            inTag = True
            continue
        elif not inTag:
            r += c
    return r.encode("utf-8")

def reIPL(ipldev):
    try:
        rc = execWithRedirect("chreipl", ["node", "/dev/" + ipldev])
    except RuntimeError as e:
        rc = True
        log.info("Unable to set reIPL device to %s: %s",
                 ipldev, e)

    if rc:
        devstring = None

        for disk in anaconda.storage.disks:
            if disk.name == ipldev:
                devstring = disk.description
                break

        if devstring is None:
            devstring = _("the device containing /boot")

        message = _("After shutdown, please perform a manual IPL from %s "
                    "to continue installation." % devstring)

        log.info("reIPL configuration failed")
        #os.kill(os.getppid(), signal.SIGUSR1)
    else:
        message = None
        log.info("reIPL configuration successful")
        #os.kill(os.getppid(), signal.SIGUSR2)

    return message

def resetRpmDb():
    for rpmfile in glob.glob("%s/var/lib/rpm/__db.*" % ROOT_PATH):
        try:
            os.unlink(rpmfile)
        except OSError as e:
            log.debug("error %s removing file: %s" %(e,rpmfile))

def parseNfsUrl(nfsurl):
    options = ''
    host = ''
    path = ''
    if nfsurl:
        s = nfsurl.split(":")
        s.pop(0)
        if len(s) >= 3:
            (options, host, path) = s[:3]
        elif len(s) == 2:
            (host, path) = s
        else:
            host = s[0]
    return (options, host, path)

def add_po_path(module, dir):
    """ Looks to see what translations are under a given path and tells
    the gettext module to use that path as the base dir """
    for d in os.listdir(dir):
        if not os.path.isdir("%s/%s" %(dir,d)):
            continue
        if not os.path.exists("%s/%s/LC_MESSAGES" %(dir,d)):
            continue
        for basename in os.listdir("%s/%s/LC_MESSAGES" %(dir,d)):
            if not basename.endswith(".mo"):
                continue
            log.info("setting %s as translation source for %s" %(dir, basename[:-3]))
            module.bindtextdomain(basename[:-3], dir)

def setup_translations(module):
    if os.path.isdir(TRANSLATIONS_UPDATE_DIR):
        add_po_path(module, TRANSLATIONS_UPDATE_DIR)
    module.textdomain("anaconda")

def fork_orphan():
    """Forks an orphan.

    Returns 1 in the parent and 0 in the orphaned child.
    """
    intermediate = os.fork()
    if not intermediate:
        if os.fork():
            # the intermediate child dies
            os._exit(0)
        return 0
    # the original process waits for the intermediate child
    os.waitpid(intermediate, 0)
    return 1

def _run_systemctl(command, service):
    """
    Runs 'systemctl command service.service'

    @return: exit status of the systemctl

    """

    service_name = service + ".service"
    ret = execWithRedirect("systemctl", [command, service_name])

    return ret

def start_service(service):
    return _run_systemctl("start", service)

def stop_service(service):
    return _run_systemctl("stop", service)

def restart_service(service):
    return _run_systemctl("restart", service)

def service_running(service):
    ret = _run_systemctl("status", service)

    return ret == 0

def dracut_eject(device):
    """
    Use dracut shutdown hook to eject media after the system is shutdown.
    This is needed because we are running from the squashfs.img on the media
    so ejecting too early will crash the installer.
    """
    if not device:
        return

    try:
        if not os.path.exists(DRACUT_SHUTDOWN_EJECT):
            f = open(DRACUT_SHUTDOWN_EJECT, "w")
            f.write("#!/bin/sh\n")
            f.write("# Created by Anaconda\n")
        else:
            f = open(DRACUT_SHUTDOWN_EJECT, "a")

        f.write("eject %s\n" % (device,))
        f.close()
        os.chmod(DRACUT_SHUTDOWN_EJECT, 0755)
        log.info("Wrote dracut shutdown eject hook for %s" % (device,))
    except Exception, e:
        log.error("Error writing dracut shutdown eject hook for %s: %s" % (device, e))

def vtActivate(num):
    """
    Try to switch to tty number $num.

    @type num: int
    @return: whether the switch was successful or not
    @rtype: bool

    """

    try:
        ret = execWithRedirect("chvt", [str(num)])
    except OSError as oserr:
        ret = -1
        log.error("Failed to run chvt: %s", oserr.strerror)

    if ret != 0:
        log.error("Failed to switch to tty%d", num)

    return ret == 0

class ProxyStringError(Exception):
    pass

class ProxyString(object):
    """ Handle a proxy url
    """
    def __init__(self, url=None, protocol="http://", host=None, port="3128",
                 username=None, password=None):
        """ Initialize with either url
        ([protocol://][username[:password]@]host[:port]) or pass host and
        optionally:

        protocol    http, https, ftp
        host        hostname without protocol
        port        port number (defaults to 3128)
        username    username
        password    password

        The str() of the object is the full proxy url

        ProxyString.url is the full url including username:password@
        ProxyString.noauth_url is the url without username:password@
        """
        self.url = url
        self.protocol = protocol
        self.host = host
        self.port = str(port)
        self.username = username
        self.password = password
        self.proxy_auth = ""
        self.noauth_url = None

        if url:
            self.parse_url()
        elif not host:
            raise ProxyStringError("No host url")
        else:
            self.parse_components()

    def parse_url(self):
        """ Parse the proxy url into its component pieces
        """
        # NOTE: If this changes, update tests/regex/proxy.py
        #
        # proxy=[protocol://][username[:password]@]host[:port][path]
        # groups
        # 1 = protocol
        # 2 = username:password@
        # 3 = username
        # 4 = password
        # 5 = hostname
        # 6 = port
        # 7 = extra
        pattern = re.compile("([A-Za-z]+://)?(([A-Za-z0-9]+)(:[^:@]+)?@)?([^:/]+)(:[0-9]+)?(/.*)?")
        m = pattern.match(self.url)
        if not m:
            raise ProxyStringError("malformed url, cannot parse it.")

        # If no protocol was given default to http.
        if m.group(1):
            self.protocol = m.group(1)
        else:
            self.protocol = "http://"

        if m.group(3):
            self.username = m.group(3)

        if m.group(4):
            # Skip the leading colon
            self.password = m.group(4)[1:]

        if m.group(5):
            self.host = m.group(5)
            if m.group(6):
                # Skip the leading colon
                self.port = m.group(6)[1:]
        else:
            raise ProxyStringError("url has no host component")

        self.parse_components()

    def parse_components(self):
        """ Parse the components of a proxy url into url and noauth_url
        """
        if self.username or self.password:
            self.proxy_auth = "%s:%s@" % (self.username or "",
                                          self.password or "")

        self.url = self.protocol + self.proxy_auth + self.host + ":" + self.port
        self.noauth_url = self.protocol + self.host + ":" + self.port

    @property
    def dict(self):
        """ return a dict of all the elements of the proxy string
        url, noauth_url, protocol, host, port, username, password
        """
        components = ["url", "noauth_url", "protocol", "host", "port",
                      "username", "password"]
        return dict([(k, getattr(self, k)) for k in components]) 

    def __str__(self):
        return self.url
