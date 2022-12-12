#!/usr/bin/env python3

from __future__ import annotations

import re
import time
import hashlib
import argparse
import subprocess
import configparser

from typing import Any
from pathlib import Path
from gi import require_version

require_version('Secret', '1')

from gi.repository import Secret


def lcut(item: str, padding: int) -> str:
    '''
    Pad the item to the specified length with spaces or cut it
    on the padding length.

    Parameters:
        item            the item to lcut

    Returns:
        cutted or padded item
    '''
    if padding is None or padding == 0:
        return item

    if len(item) < padding:
        return item.ljust(padding)

    else:
        return item[:padding - 4] + '..  '


def parse_qube_list(qube_list: str) -> list[str]:
    '''
    Parse a list of qube names as it is specified within the KeePass notes

    Parameters:
        qube_list           list in string representation

    Returns:
        parsed list of qube names
    '''
    if not qube_list:
        return []

    qubes = []

    for item in qube_list.split(','):
        qubes.append(item.strip())

    return qubes


def contains_qube(qube_list: list, qube: str) -> bool:
    '''
    Checks whether the specified qube is contained within the specified qube list.

    Parameters:
        qube_list           list of qube names or compiled regular expressions
        qube                qube name to check for

    Returns:
        true if the specified qube name is contained within the list
    '''
    if not qube_list:
        return False

    for entry in qube_list:

        if type(entry) == str and entry == qube:
            return True

        elif entry.fullmatch(qube):
            return True

    return False


class RofiAbortedException(Exception):
    '''
    Custom exception class.
    '''


class MissingConfigException(Exception):
    '''
    Custom exception class.
    '''


class Config:
    '''
    Class for parsing the qubes-keepass configuration file.
    '''
    parser = None
    restricted = []
    unrestricted = []

    config_locations = [
                         Path.home() / '.config/qubes-keepass.ini',
                         Path.home() / '.config/qubes-keepass/config.ini',
                         Path.home() / '.config/qubes-keepass/qubes-keepass.ini',
                         Path('/etc/qubes-keepass.ini'),
                         Path('/etc/qubes-keepass/config.ini'),
                         Path('/etc/qubes-keepass/qubes-keepass.ini'),
                       ]

    default_trust_order = [
                             'red',
                             'orange',
                             'yellow',
                             'green',
                             'gray',
                             'blue',
                             'purple',
                             'black',
                          ]

    def get(key: str) -> str:
        '''
        Get the specified key from the configuration file. Currently, only
        unique keys are present and sections are only used for formatting.
        Therefore we can simply iterate over each section to find the key.

        Parameters:
            key             key to obtain from the configuration file

        Returns:
            value for the specified key
        '''
        for section in Config.parser.sections():

            value = Config.parser[section].get(key)

            if value is not None:

                if value == '':
                    return None

                return value

        raise KeyError(key)

    def getboolean(key: str) -> bool:
        '''
        Same as get, but returns bool.

        Parameters:
            key             key to obtain from the configuration file

        Returns:
            value for the specified key
        '''
        for section in Config.parser.sections():

            value = Config.parser[section].getboolean(key)

            if value is not None:
                return value

        raise KeyError(key)

    def getint(key: str) -> int:
        '''
        Same as get, but returns int.

        Parameters:
            key             key to obtain from the configuration file

        Returns:
            value for the specified key
        '''
        for section in Config.parser.sections():

            value = Config.parser[section].getint(key)

            if value is not None:
                return value

        raise KeyError(key)

    def get_rofi_options() -> list[str]:
        '''
        Return the configured rofi options as a list that can be used for
        the subprocess module.

        Parameters:
            None

        Returns:
            list of rofi options.
        '''
        return list(Config.parser['rofi.options'].values())

    def translate_trust(trust_level: int) -> int:
        '''
        Translates the qubes specific numerical trust value to a user defined
        trust value according to the configuration file.

        Parameters:
            trust_level         the numerical trust level to translate

        Returns:
            numerical trust level according to the user configuration
        '''
        try:
            color_value = Config.default_trust_order[trust_level - 1]
            return Config.getint(f'trust_level_{color_value}')

        except (IndexError, KeyError):
            return 0

    def is_trusted(trust_level: int, threshold: int) -> bool:
        '''
        Checks whether the specified trust level is considered trusted using
        the given threshold. This function could be trivial when just taking
        the qubes predefined ordering of trust levels. However, users may use
        their own ordering that can be configured within the configuration
        file.

        Parameters:
            trust_level         the trust level to check against the threshold
            threshold           the threshould

        Returns:
            True if the specified trust level is considered trusted
        '''
        if trust_level is None:
            return True

        translated_trust = Config.translate_trust(trust_level)

        if translated_trust >= threshold:
            return True

        return False

    def load(path: str = None) -> Config:
        '''
        Create a Config object from the specified path or,
        if None was specified, from certain default locations.

        Parameters:
            path            path of a qubes-keepass configuration file

        Returns:
            Config
        '''
        if path is not None:
            config_path = Path(path)

        else:

            for path in Config.config_locations:

                if path.is_file():
                    config_file = path
                    break
        
        if not config_file.is_file():
            raise MissingConfigException('No config file found.')

        Config.parser = configparser.ConfigParser()
        Config.parser.read(config_file)

        Config.restricted = parse_qube_list(Config.get('restricted'))
        Config.unrestricted = parse_qube_list(Config.get('unrestricted'))


class Credential:
    '''
    Represents a credential entry present within KeePass.
    '''

    def __init__(self, item: Secret.Item, service: Secret.Service) -> None:
        '''
        Initialize the Credential object with an Secret.Item object
        obtained via DBus.

        Parameters:
            item            Secret.Item obtained from KeePass
            service         the Secret.Service DBus connection

        Returns:
            None
        '''
        self.item = item
        self.service = service
        self.attributes = item.get_attributes()

        self.url = self.attributes.get('URL')
        self.uuid = self.attributes.get('Uuid')
        self.path = Path(self.attributes.get('Path'))
        self.title = self.attributes.get('Title')
        self.notes = self.attributes.get('Notes')
        self.username = self.attributes.get('UserName')

        settings = self.parse_settings()

        self.qubes = parse_qube_list(settings.get('qubes'))
        self.trust = int(settings.get('trust', 0))
        self.timeout = int(settings.get('timeout', Config.get('timeout')))

        if Config.getboolean('regex') and self.qubes is not None:
            self.qubes = list(map(re.compile, self.qubes))

    def __str__(self) -> str:
        '''
        The string representation of a Credential object is just it's
        list of attributes.

        Parameters:
            None

        Returns:
            default dictionary output of the Credential attributes
        '''
        return str(self.attributes)

    def __eq__(self, other: Any) -> bool:
        '''
        Two Credential entries are equal, if their Uuid matches.

        Parameters:
            other           object to compare with

        Returns:
            true if Credentials are equal, false otherwise
        '''
        if type(self) != type(other):
            return False

        return self.uuid == other.uuid

    def parse_settings(self) -> dict():
        '''
        Parses the Notes section of the credential for Qubes specific
        settings and returns them as a dict.

        Parameters:
            None

        Returns:
            settings dictionary
        '''
        settings = {}
        lines = self.notes.split('\n')

        if lines[0].lower().replace('-', '') != '[qubeskeepass]':
            return dict()

        for line in lines[1:]:

            try:
                setting, value = line.split('=', 1)

                setting = setting.strip()
                value = value.strip()

                settings[setting] = value

            except ValueError:
                break

        return settings

    def get_secret(self) -> str:
        '''
        Obtain the secret for the credential.

        Parameters:
            None

        Returns:
            secret for the credential
        '''
        if self.item.locked:
            self.service.unlock_sync([self.item])

        self.item.load_secret_sync()
        return self.item.get_secret().get_text()

    def copy_to_qube(self, attribute: int, qube: str, trust_level: int) -> None:
        '''
        Copy the specified attribute to the specified qube. If the credential
        has a dedicated qube assigned, the operation might fail when a different
        qube is selected.

        After the requested attribute was copied, the function sleeps for the
        timeout value specified within the credential. If no other copy operation
        occured within this time, the clipboard of the specified Qube is cleared.

        Parameters:
            attribute       the Credential attribute to copy
            qube            the qube to copy the credential to
            trust_level     the trust level of the target qube

        Returns:
            None
        '''
        if self.trust and not Config.is_trusted(trust_level, self.trust):
            return

        if not Config.is_trusted(trust_level, Config.getint('minimum_trust')):
            return

        if self.qubes and not contains_qube(self.qubes, qube):
            print(f'[-] Copy operation blocked. Selected credential is not allowed for {qube}.')
            return

        if not self.qubes:

            if Config.restricted and contains_qube(Config.restricted, qube):
                print(f'[-] Copy operation blocked. {qube} is a restricted qube.')
                return

            if Config.unrestricted and not contains_qube(Config.unrestricted, qube):
                print(f'[-] Copy operation blocked. {qube} is a restricted qube.')
                return

        value = ''

        if attribute == 0 or attribute == 10:
            print(f'[+] Copying password of credential {self.title} to {qube}.')
            value = self.get_secret().encode()

        elif attribute == 11:
            print(f'[+] Copying username of credential {self.title} to {qube}.')
            value = self.username.encode()

        elif attribute == 12:
            print(f'[+] Copying url of credential {self.title} to {qube}.')
            value = self.url.encode()

        process = subprocess.Popen(['qrexec-client-vm', qube, 'custom.QubesKeepass'], stdin=subprocess.PIPE)
        process.stdin.write(value)
        process.stdin.close()

        qube_hash = hashlib.md5(qube.encode()).hexdigest()
        lockfile = Path.home() / f'qubes-keepass-{qube_hash}.lock'

        lockfile.touch()
        timestamp = lockfile.stat().st_mtime

        print(f'[+] Sleeping for {self.timeout} seconds.')

        time.sleep(self.timeout)
        timestamp2 = lockfile.stat().st_mtime

        if timestamp != timestamp2:
            print('[+] Another copy operation occured. Not cleaning the clipboard.')
            return

        else:
            lockfile.unlink()

            process = subprocess.Popen(['qrexec-client-vm', qube, 'custom.QubesKeepass'], stdin=subprocess.PIPE)
            process.stdin.write(''.encode())
            process.stdin.close()

            print(f'[+] Clipboard of {qube} cleared.')


class CredentialCollection:
    '''
    Represents a collection of Credential objects.
    '''

    def __init__(self, credentials: list[Credential]) -> None:
        '''
        Initialize a CredentialCollection with a list of credentials.

        Parameters:
            credentials         the credentials to include into the collection

        Returns:
            None
        '''
        self.credentials = sorted(credentials, key=lambda x: x.path.parent)

    def filter_credentials(self, qube: str, trust_level: int) -> None:
        '''
        Filter the list of credentials for the specified qube. Filtered
        credentials include all credentials with a matching qube name
        and credentials without qube specification. Restricted qubes can
        only obtain credentials that explicitly target these qubes.

        Parameters:
            qube                 qube to filter for
            trust_level          trust level of the qube

        Returns:
            None
        '''
        filtered = []

        for cred in self.credentials:

            if cred.trust and not Config.is_trusted(trust_level, cred.trust):
                continue

            if not Config.is_trusted(trust_level, Config.getint('minimum_trust')):
                continue

            if cred.qubes and contains_qube(cred.qubes, qube):
                filtered.append(cred)

            elif not cred.qubes:

                if Config.unrestricted and not contains_qube(Config.unrestricted, qube):
                    continue

                elif Config.restricted and contains_qube(Config.restricted, qube):
                    continue

                filtered.append(cred)

        self.credentials = filtered

    def __str__(self) -> str:
        '''
        The string representiation of a CredentialCollection is a formatted list
        that can be displayed within rogi.

        Parameters:
            credentials         list of credentials to display

        Returns:
            None
        '''
        formatted = ''

        for credential in self.credentials:

            folder = credential.path.parent.name or 'Root'

            formatted += lcut(credential.title, Config.getint('title_length'))
            formatted += lcut(folder, Config.getint('folder_length'))
            formatted += lcut(credential.username, Config.getint('username_length'))
            formatted += lcut(credential.url, Config.getint('url_length'))
            formatted += '\n'

        return formatted

    def display_rofi(self, qube: str = 'Qube') -> (int, Credential):
        '''
        Displays the contained credentials within rofi and waits for a user
        selection. The selected Credential and the exit value of rofi are
        returned.

        Parameters:
            qube        the qube name to copy the password to

        Returns:
            Credential item selected by the user and exit code
        '''
        rofi_mesg = f'Selected credential is copied to <b>{qube}</b>\n\n'
        rofi_mesg += lcut('Title', Config.getint('title_length'))
        rofi_mesg += lcut('Folder', Config.getint('folder_length'))
        rofi_mesg += lcut('Username', Config.getint('username_length'))
        rofi_mesg += lcut('URL', Config.getint('url_length'))

        mappings = ['-kb-custom-1', Config.get('copy_password')]
        mappings += ['-kb-custom-2', Config.get('copy_username')]
        mappings += ['-kb-custom-3', Config.get('copy_url')]

        print('[+] Starting rofi.')
        process = subprocess.Popen(['rofi'] + Config.get_rofi_options() + ['-mesg', rofi_mesg] + mappings,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE)

        try:
            selected = process.communicate(input=str(self).encode())[0]
            selected = int(selected.decode().strip())

            if selected == -1:
                raise RofiAbortedException('User selected empty credential')

        except ValueError:
            raise RofiAbortedException('rofi selection was aborted by user')

        print(f'[+] User selected {self.credentials[selected].title} with return code {process.returncode}')
        return (process.returncode, self.credentials[selected])

    def load(service: Secret.Service) -> CredentialCollection:
        '''
        Load credential information via DBus and create a new CredentialCollection
        from it.

        Parameters:
            service         DBus connection to the Secret Service

        Returns:
            None
        '''
        credentials = []
        collection = Secret.Collection.for_alias_sync(service, "default", Secret.CollectionCreateFlags.NONE, None)

        for item in collection.get_items():
            credential = Credential(item, service)
            credentials.append(credential)

        return CredentialCollection(credentials)


parser = argparse.ArgumentParser(description='''qubes-keepass v1.0.0 - A rofi based KeePassXC frontend for Qubes''')
parser.add_argument('qube', help='qube to copy the credential to')
parser.add_argument('--trust-level', type=int, help='numerical trust level of the qube')
parser.add_argument('--config', help='path to the configuration file')


def main() -> None:
    '''
    Main function. Ask the user for a credential to copy and copy it into
    the specified Qube.

    Parameters:
        None

    Returns:
        None
    '''
    args = parser.parse_args()

    try:
        Config.load(args.config)

    except MissingConfigException:
        print('[-] Unable to find the qubes-keepass.ini configuration file.')
        return

    except KeyError as e:
        print(f'[-] Missing required key {str(e)} in configuration file.')
        return

    if Config.restricted and Config.unrestricted:
        print("[-] The configuration options 'restricted' and 'unrestricted' are mutually exclusive.")
        print('[-] Configure only one of them and leave the other empty to continue.')
        return

    try:
        service = Secret.Service.get_sync(Secret.ServiceFlags.OPEN_SESSION | Secret.ServiceFlags.LOAD_COLLECTIONS)

    except Exception as e:
        print('[-] Unable to get Secret Service connection.')
        print('[-] Error Message: ' + str(e))
        return

    try:
        if Config.getboolean('regex'):

            for lst in [Config.restricted, Config.unrestricted]:
                compiled = list(map(re.compile, lst))
                lst.clear()
                lst += compiled

        collection = CredentialCollection.load(service)
        collection.filter_credentials(args.qube, args.trust_level)

        attr, credential = collection.display_rofi(args.qube)
        credential.copy_to_qube(attr, args.qube, args.trust_level)

    except KeyError as e:
        print(f'[-] Missing required key {str(e)} in configuration file.')
        return

    except re.error as e:
        print('[-] Regex error. Encountered an invalid regular expression.')
        print('[-] Error message: ' + str(e))
        return

    except RofiAbortedException:
        print('[+] Aborted.')
        return


main()
