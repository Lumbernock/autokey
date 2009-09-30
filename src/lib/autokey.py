#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright (C) 2008 Chris Dekter

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 675 Mass Ave, Cambridge, MA 02139, USA.


import sys, traceback, os.path, signal, logging, logging.handlers, subprocess, Queue, optparse
import gettext, gtk
gettext.install("autokey")

import service, ui.notifier, ui.popupmenu, ui.configwindow, ui.abbrselector
from configmanager import *

CONFIG_DIR = os.path.expanduser("~/.config/autokey")
LOCK_FILE = CONFIG_DIR + "/autokey.pid"
LOG_FILE = CONFIG_DIR + "/autokey.log"
MAX_LOG_SIZE = 5 * 1024 * 1024 # 5 megabytes
MAX_LOG_COUNT = 3
LOG_FORMAT = "%(levelname)s - %(name)s - %(message)s"

APP_NAME = "AutoKey"
CATALOG = ""
PROGRAM_NAME = _("AutoKey")
VERSION = "0.60.6"
DESCRIPTION = _("Desktop automation utility")
#LICENSE = KAboutData.License_GPL_V3
COPYRIGHT = _("(c) 2009 Chris Dekter")
#TEXT = _("")
HOMEPAGE  = "http://autokey.sourceforge.net/"
BUG_EMAIL = "cdekter@gmail.com"



class Application:
    """
    Main application class; starting and stopping of the application is controlled
    from here, together with some interactions from the tray icon.
    """
    
    def __init__(self):
        
        """aboutData = KAboutData(APP_NAME, CATALOG, PROGRAM_NAME, VERSION, DESCRIPTION,
                                    LICENSE, COPYRIGHT, TEXT, HOMEPAGE, BUG_EMAIL)

        aboutData.addAuthor(_("Chris Dekter"), _("Developer"), "cdekter@gmail.com", "")
        aboutData.addAuthor(_("Sam Peterson"), _("Original developer"), "peabodyenator@gmail.com", "")
        aboutData.setProgramIconName(ui.notifier.ICON_FILE)
        
        KCmdLineArgs.init(sys.argv, aboutData)
        options = KCmdLineOptions()
        options.add("l").add("verbose", _("Enable verbose logging"))
        options.add("c").add("configure", _("Show the configuration window on startup"))
        KCmdLineArgs.addCmdLineOptions(options)
        args = KCmdLineArgs.parsedArgs()"""
        
        gtk.gdk.threads_init()
        
        p = optparse.OptionParser()
        p.add_option("-l", "--verbose", help="Enable verbose logging", action="store_true", default=False)
        p.add_option("-c", "--configure", help="Show the configuration window on startup", action="store_true", default=False)
        options, args = p.parse_args()
        
        try:
            # Create configuration directory
            if not os.path.exists(CONFIG_DIR):
                os.makedirs(CONFIG_DIR)
            # Initialise logger
            rootLogger = logging.getLogger()
            
            if options.verbose:
                rootLogger.setLevel(logging.DEBUG)
                handler = logging.StreamHandler(sys.stdout)
            else:
                rootLogger.setLevel(logging.INFO)
                handler = logging.handlers.RotatingFileHandler(LOG_FILE, 
                                        maxBytes=MAX_LOG_SIZE, backupCount=MAX_LOG_COUNT)
            
            handler.setFormatter(logging.Formatter(LOG_FORMAT))
            rootLogger.addHandler(handler)
            
            
            if self.__verifyNotRunning():
                self.__createLockFile()
                
            self.initialise(options.configure)
            
        except Exception, e:
            self.show_error_dialog(_("Fatal error starting AutoKey.\n") + str(e))
            logging.exception("Fatal error starting AutoKey: " + str(e))
            sys.exit(1)
            
            
    def __createLockFile(self):
        f = open(LOCK_FILE, 'w')
        f.write(str(os.getpid()))
        f.close()
        
    def __verifyNotRunning(self):
        if os.path.exists(LOCK_FILE):
            f = open(LOCK_FILE, 'r')
            pid = f.read()
            f.close()
            
            # Check that the found PID is running and is autokey
            p = subprocess.Popen(["ps", "-p", pid, "-o", "command"], stdout=subprocess.PIPE)
            p.wait()
            output = p.stdout.readlines()
            if len(output) > 1:
                # process exists
                if "autokey" in output[1]:
                    logging.error("AutoKey is already running - exiting")
                    self.show_error_dialog(_("AutoKey is already running as pid: ") + pid)
                    sys.exit(1)
         
        return True

    def main(self):
        gtk.main()

    def initialise(self, configure):
        logging.info("Initialising application")
        self.configManager = get_config_manager(self)
        self.service = service.Service(self)
        self.serviceDisabled = False
        
        try:
            self.service.start()
        except Exception, e:
            logging.exception("Error starting interface: " + str(e))
            self.serviceDisabled = True
            self.show_error_dialog(_("Error starting interface. Keyboard monitoring will be disabled.\n" +
                                    "Check your system/configuration."), str(e))
        
        self.notifier = ui.notifier.Notifier(self)
        self.configWindow = None
        self.abbrPopup = None
        
        if ConfigManager.SETTINGS[IS_FIRST_RUN] or configure:
            ConfigManager.SETTINGS[IS_FIRST_RUN] = False
            self.show_configure()
            
    def init_global_hotkeys(self, configManager):
        logging.info("Initialise global hotkeys")
        configManager.toggleServiceHotkey.set_closure(self.toggle_service)
        configManager.configHotkey.set_closure(self.show_configure_async)
        configManager.showPopupHotkey.set_closure(self.show_abbr_async)        
        
    def config_altered(self):
        self.configManager.config_altered()
        #self.notifier.build_menu()
        
    def unpause_service(self):
        """
        Unpause the expansion service (start responding to keyboard and mouse events).
        """
        self.service.unpause()
        self.notifier.update_tool_tip()
    
    def pause_service(self):
        """
        Pause the expansion service (stop responding to keyboard and mouse events).
        """
        self.service.pause()
        self.notifier.update_tool_tip()
        
    def toggle_service(self):
        """
        Convenience method for toggling the expansion service on or off.
        """
        if self.service.is_running():
            self.pause_service()
        else:
            self.unpause_service()
            
    def shutdown(self):
        """
        Shut down the entire application.
        """
        if self.configWindow is not None:
            if self.configWindow.promptToSave():
               return
             
        logging.info("Shutting down")
        self.service.shutdown()
        gtk.main_quit()
        os.remove(LOCK_FILE)
            
    def show_notify(self, message, isError=False, details=''):
        """
        Show a notification popup.
        
        @param message: Message to show in the popup
        @param isError: Whether the message is an error (shows with an error icon)
        @param details: Error details, which the user can view in a dialog by clicking
        the "View Details" button.
        """
        self.notifier.show_notify(message, isError, details)
        
    def show_configure(self):
        """
        Show the configuration window, or deiconify (un-minimise) it if it's already open.
        """
        logging.info("Displaying configuration window")
        if self.configWindow is None:
            self.configWindow = ui.configwindow.ConfigWindow(self)
            self.configWindow.show()
        else:    
            self.configWindow.deiconify()
            
    def show_configure_async(self):
        gtk.gdk.threads_enter()
        self.show_configure()
        gtk.gdk.threads_leave()

    def show_abbr_selector(self):
        """
        Show the abbreviation autocompletion popup.
        """
        if self.abbrPopup is None:
            logging.info("Displaying abbreviation popup")
            self.abbrPopup = ui.abbrselector.AbbrSelectorDialog(self)
            self.abbrPopup.present()
            
    def show_abbr_async(self):
        gtk.gdk.threads_enter()
        self.show_abbr_selector()
        gtk.gdk.threads_leave()
                
    def main(self):
        logging.info("Entering main()")
        gtk.main()
            
    def show_error_dialog(self, message, details=None):
        """
        Convenience method for showing an error dialog.
        """
        dlg = gtk.MessageDialog(type=gtk.MESSAGE_ERROR, buttons=gtk.BUTTONS_OK,
                                 message_format=message)
        if details is not None:
            dlg.format_secondary_text(details)
        dlg.run()
        dlg.destroy()
        
    def show_popup_menu(self, folders=[], items=[], onDesktop=True, title=None):
        self.menu = ui.popupmenu.PopupMenu(self.service, folders, items, onDesktop, title)
        self.menu.show_on_desktop()
    
    def hide_menu(self):
        self.menu.remove_from_desktop()
