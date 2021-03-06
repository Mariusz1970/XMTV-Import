#!/usr/bin/python
#
# This file no longer has a direct link to Enigma2, allowing its use anywhere
# you can supply a similar interface. See plugin.py and OfflineImport.py for
# the contract.
#
from Components.Console import Console
from Components.config import config
from twisted.internet import reactor, threads
from twisted.web.client import downloadPage
import twisted.python.runtime

import time, os, gzip, log

try:
	from Components.SwapCheck import SwapCheck
	swapcheckimport = True
except:
	swapcheckimport = False
	
HDD_EPG_DAT = config.misc.epgcache_filename.value

PARSERS = {
#	'radiotimes': 'uk_radiotimes',
	'xmltv': 'gen_xmltv',
	'genxmltv': 'gen_xmltv',
#	'mythxmltv': 'myth_xmltv',
#	'nlwolf': 'nl_wolf'
}

def relImport(name):
	fullname = __name__.split('.')
	fullname[-1] = name
	mod = __import__('.'.join(fullname))
	for n in fullname[1:]:
		mod = getattr(mod, n)
	return mod

def getParser(name):
	module = PARSERS.get(name, name)
	mod = relImport(module)
	return mod.new()

def getTimeFromHourAndMinutes(hour, minute):
	now = time.localtime()
	begin = int(time.mktime((now.tm_year, now.tm_mon, now.tm_mday, hour, minute, 0, now.tm_wday, now.tm_yday, now.tm_isdst)))
	return begin

def bigStorage(minFree, default, *candidates):
		mounts = open('/proc/mounts', 'rb').readlines()
		# format: device mountpoint fstype options #
		mountpoints = [x.split(' ', 2)[1] for x in mounts]
		for candidate in candidates:
			if candidate in mountpoints:
				try:
					diskstat = os.statvfs(candidate)
					free = diskstat.f_bfree * diskstat.f_bsize
					if free > minFree:
						return candidate
				except:
					pass
		return default

class OudeisImporter:
	'Wrapper to convert original patch to new one that accepts multiple services'
	def __init__(self, epgcache):
		self.epgcache = epgcache
	# difference with old patch is that services is a list or tuple, this
	# wrapper works around it.
	def importEvents(self, services, events):
		for service in services:
			self.epgcache.importEvent(service, events)

class XMLTVImport:
	"""Simple Class to import EPGData"""

	def __init__(self, epgcache, channelFilter):
		self.eventCount = None
		self.epgcache = None
		self.storage = None
		self.sources = []
		self.source = None
		self.epgsource = None
		self.fd = None
		self.iterator = None
		self.onDone = None
		self.epgcache = epgcache
		self.channelFilter = channelFilter

	def beginImport(self, longDescUntil = None):
		'Starts importing using Enigma reactor. Set self.sources before calling this.'
		if hasattr(self.epgcache, 'importEvents'):
			self.storage = self.epgcache
		elif hasattr(self.epgcache, 'importEvent'):
			self.storage = OudeisImporter(self.epgcache)
		else:
			print "[XMLTVImport] oudeis patch not detected, using epg.dat instead."
			import epgdat_importer
			self.storage = epgdat_importer.epgdatclass()
		self.eventCount = 0
		if longDescUntil is None:
			# default to 7 days ahead
			self.longDescUntil = time.time() + 24*3600*7
		else:
			self.longDescUntil = longDescUntil;
		self.nextImport()

	def nextImport(self):
		self.closeReader()
		if not self.sources:
			self.closeImport()
			return
		self.source = self.sources.pop()
		print>>log, "[XMLTVImport] nextImport, source=", self.source.description
		filename = self.source.url
		if filename.startswith('http:') or filename.startswith('ftp:'):
			self.do_download(filename)
		else:
			if swapcheckimport:
				self.MemCheck(None, filename, False)
			else:
				self.MemCheck1(None, filename, deleteFile=False)

	def createIterator(self):
		self.source.channels.update(self.channelFilter)
		return getParser(self.source.parser).iterator(self.fd, self.source.channels.items)

	def readEpgDatFile(self, filename, deleteFile=False):
		if not hasattr(self.epgcache, 'load'):
			print>>log, "[XMLTVImport] Cannot load EPG.DAT files on unpatched enigma. Need CrossEPG patch."
			return
		try:
			os.unlink(HDD_EPG_DAT)
		except:
			pass # ignore...
		try:
			if filename.endswith('.gz'):
				print>>log, "[XMLTVImport] Uncompressing", filename
				import shutil
				fd = gzip.open(filename, 'rb')
				epgdat = open(HDD_EPG_DAT, 'wb')
				shutil.copyfileobj(fd, epgdat)
				del fd
				epgdat.close()
				del epgdat
			else:
				if filename != HDD_EPG_DAT:
					os.symlink(filename, HDD_EPG_DAT)
			print>>log, "[XMLTVImport] Importing", HDD_EPG_DAT
			self.epgcache.load()
			if deleteFile:
				try:
					os.unlink(filename)
				except:
					pass # ignore...
		except Exception, e:
		    print>>log, "[XMLTVImport] Failed to import %s:" % filename, e

	def MemCheck(self, result, filename, deleteFile):
		SwapCheck(self.MemCheckCallback, [result, filename, deleteFile])

	def MemCheckCallback(self, callbackArgs):
		(result, filename, deleteFile) = callbackArgs
		self.afterDownload(result, filename, deleteFile)

	def MemCheck1(self, result, filename, deleteFile=False):
		self.swapdevice = ""
		self.Console = Console()
		self.swapdevice = os.path.split(filename)
		self.swapdevice = self.swapdevice[0]
		print>>log, "[XMLTVImport] SwapFile location",self.swapdevice
		if os.path.exists(self.swapdevice + "/swapfile_xmltv"):
			print>>log, "[XMLTVImport] Removing old swapfile."
			self.Console.ePopen("swapoff " + self.swapdevice + "/swapfile_xmltv && rm " + self.swapdevice + "/swapfile_xmltv")
		f = open('/proc/meminfo', 'r')
		for line in f.readlines():
			if line.find('MemFree') != -1:
				parts = line.strip().split()
				memfree = int(parts[1])
			elif line.find('SwapFree') != -1:
				parts = line.strip().split()
				swapfree = int(parts[1])
		f.close()
		TotalFree = memfree + swapfree
		print>>log, "[XMLTVImport] Free Mem",TotalFree
		if int(TotalFree) < 5000:
			print>>log, "[XMLTVImport] Not Enough Ram"
			self.MemCheck2(filename, deleteFile)
		else:
			print>>log, "[XMLTVImport] Found Enough Ram"
			self.afterDownload(None, filename, deleteFile)

	def MemCheck2(self, filename, deleteFile):
		print>>log, "[XMLTVImport] Creating Swapfile."
		self.Console.ePopen("dd if=/dev/zero of=" + self.swapdevice + "/swapfile_xmltv bs=1024 count=16440", self.MemCheck3, [filename, deleteFile])

	def MemCheck3(self, result, retval, extra_args = None):
		(filename, deleteFile) = extra_args
		if retval == 0:
			self.Console.ePopen("mkswap " + self.swapdevice + "/swapfile_xmltv", self.MemCheck4, [filename, deleteFile])

	def MemCheck4(self, result, retval, extra_args = None):
		(filename, deleteFile) = extra_args
		if retval == 0:
			self.Console.ePopen("swapon " + self.swapdevice + "/swapfile_xmltv", self.MemCheck5, [filename, deleteFile])

	def MemCheck5(self, result, retval, extra_args = None):
		(filename, deleteFile) = extra_args
		self.afterDownload(None, filename, deleteFile)

	def afterDownload(self, result, filename, deleteFile=False):
		if os.path.getsize(filename) > 0:
			print>>log, "[XMLTVImport] afterDownload", filename
			if self.source.parser == 'epg.dat':
				if twisted.python.runtime.platform.supportsThreads():
					print>>log, "[XMLTVImport] Using twisted thread for DAT file"
					threads.deferToThread(self.readEpgDatFile, filename, deleteFile).addCallback(lambda ignore: self.nextImport())
				else:
					self.readEpgDatFile(filename, deleteFile)
				return
			if filename.endswith('.gz'):
				self.fd = gzip.open(filename, 'rb')
			else:
				self.fd = open(filename, 'rb')
			if twisted.python.runtime.platform.supportsThreads():
				print>>log, "[XMLTVImport] Using twisted thread!"
				threads.deferToThread(self.doThreadRead).addCallback(lambda ignore: self.nextImport())
			else:
				self.iterator = self.createIterator()
				reactor.addReader(self)
			if deleteFile:
				try:
					print>>log, "[XMLTVImport] unlink", filename
					os.unlink(filename)
				except Exception, e:
					print>>log, "[XMLTVImport] warning: Could not remove '%s' intermediate" % filename, e
		else:
			failure = "File downloaded was zero bytes."
			self.downloadFail(failure)

	def fileno(self):
		if self.fd is not None:
			return self.fd.fileno()

	def doThreadRead(self):
		'This is used on PLi with threading'
		for data in self.createIterator():
			if data is not None:
				self.eventCount += 1
				try:
					r,d = data
					if d[0] > self.longDescUntil:
						# Remove long description (save RAM memory)
						d = d[:4] + ('',) + d[5:]
					self.storage.importEvents(r, (d,))
				except Exception, e:
					print>>log, "[XMLTVImport] ### importEvents exception:", e
		print>>log, "[XMLTVImport] ### thread is ready ### Events:", self.eventCount

	def doRead(self):
		'called from reactor to read some data'
		try:
			# returns tuple (ref, data) or None when nothing available yet.
			data = self.iterator.next()
			if data is not None:
				self.eventCount += 1
				try:
					r,d = data
					if d[0] > self.longDescUntil:
						# Remove long description (save RAM memory)
						d = d[:4] + ('',) + d[5:]
					self.storage.importEvents(r, (d,))
				except Exception, e:
					print>>log, "[XMLTVImport] importEvents exception:", e
		except StopIteration:
			self.nextImport()

	def connectionLost(self, failure):
		'called from reactor on lost connection'
		# This happens because enigma calls us after removeReader
		print>>log, "[XMLTVImport] connectionLost", failure

	def downloadFail(self, failure):
		print>>log, "[XMLTVImport] download failed:", failure
		self.nextImport()

	def logPrefix(self):
		return '[XMLTVImport]'

	def closeReader(self):
		if self.fd is not None:
			reactor.removeReader(self)
			self.fd.close()
			self.fd = None
			self.iterator = None

	def closeImport(self):
		self.closeReader()
		self.iterator = None
		self.source = None
		if hasattr(self.storage, 'epgfile'):
			needLoad = self.storage.epgfile
		else:
			needLoad = None
		self.storage = None
		if self.eventCount is not None:
			print>>log, "[XMLTVImport] imported %d events" % self.eventCount
			reboot = False
			if self.eventCount:
				if needLoad:
					print>>log, "[XMLTVImport] no Oudeis patch, load(%s) required" % needLoad
					reboot = True
					try:
						if hasattr(self.epgcache, 'load'):
							print>>log, "[XMLTVImport] attempt load() patch"
							if needLoad != HDD_EPG_DAT:
								os.symlink(needLoad, HDD_EPG_DAT)
							self.epgcache.load()
							reboot = False
							try:
								os.unlink(needLoad)
							except:
								pass # ignore...
					except Exception, e:
						print>>log, "[XMLTVImport] load() failed:", e
			if self.onDone:
				self.onDone(reboot=reboot, epgfile=needLoad)
		self.eventCount = None
		print>>log, "[XMLTVImport] #### Finished ####"
		import glob
		for filename in glob.glob('/tmp/*.xml'):
			os.remove(filename)
		if swapcheckimport:
			SwapCheck().RemoveSwap()
		else:
			if os.path.exists(self.swapdevice + "/swapfile_xmltv"):
				print>>log, "[XMLTVImport] Removing Swapfile."
				self.Console.ePopen("swapoff " + self.swapdevice + "/swapfile_xmltv && rm " + self.swapdevice + "/swapfile_xmltv")

	def isImportRunning(self):
		return self.source is not None

	def do_download(self,sourcefile):
		path = bigStorage(9000000, '/tmp', '/media/hdd', '/media/usb', '/media/cf')
		filename = os.path.join(path, 'xmltvimport')
		if sourcefile.endswith('.gz'):
			filename += '.gz'
		sourcefile = sourcefile.encode('utf-8')
		print>>log, "[XMLTVImport] Downloading: " + sourcefile + " to local path: " + filename
		if swapcheckimport:
			downloadPage(sourcefile, filename).addCallbacks(self.MemCheck, self.downloadFail, callbackArgs=(filename,True))
		else:
			downloadPage(sourcefile, filename).addCallbacks(self.MemCheck1, self.downloadFail, callbackArgs=(filename,True))
		return filename
