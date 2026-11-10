"""Deliver ONIX files to recipients over SFTP, FTP or FTPS, and test connections.

Nielsen, Gardners and most aggregators give each data provider an SFTP (sometimes FTP) account
and a folder to drop files into. Nothing here is recipient-specific: the host, port, credentials
and folder come from the Recipient record, which the recipient's onboarding email supplies.
"""
from __future__ import annotations

import ftplib
import io
import posixpath
import socket
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import models as m

DEFAULT_PORTS = {"sftp": 22, "ftp": 21, "ftps": 21}


class DeliveryError(Exception):
    pass


def _sftp_client(r: m.Recipient):
    import paramiko
    if not r.host:
        raise DeliveryError("No host set for this recipient.")
    try:
        sock = socket.create_connection((r.host, int(r.port or 22)), timeout=20)
    except (socket.gaierror, socket.timeout, OSError) as e:
        raise DeliveryError(f"Could not reach {r.host}:{r.port or 22} ({e}).")
    transport = paramiko.Transport(sock)
    transport.banner_timeout = 20
    transport.auth_timeout = 20
    try:
        if r.private_key and r.private_key.strip():
            key = None
            for cls in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
                try:
                    key = cls.from_private_key(io.StringIO(r.private_key.strip()), password=r.password or None)
                    break
                except Exception:
                    continue
            if key is None:
                raise DeliveryError("The private key could not be read (expected an OpenSSH Ed25519, RSA or ECDSA key).")
            transport.connect(username=r.username or "", pkey=key)
        else:
            transport.connect(username=r.username or "", password=r.password or "")
    except paramiko.AuthenticationException:
        transport.close()
        raise DeliveryError("The server rejected the username, password or key.")
    except (socket.gaierror, socket.timeout, OSError, paramiko.SSHException) as e:
        transport.close()
        raise DeliveryError(f"Could not open an SFTP session with {r.host}:{r.port or 22} ({e}).")
    sftp = paramiko.SFTPClient.from_transport(transport)
    sftp.get_channel().settimeout(60)
    return transport, sftp


def _ftp_client(r: m.Recipient) -> ftplib.FTP:
    if not r.host:
        raise DeliveryError("No host set for this recipient.")
    try:
        if r.delivery == "ftps":
            ftp = ftplib.FTP_TLS(timeout=30)
            ftp.connect(r.host, int(r.port or 21))
            ftp.login(r.username or "anonymous", r.password or "")
            ftp.prot_p()
        else:
            ftp = ftplib.FTP(timeout=30)
            ftp.connect(r.host, int(r.port or 21))
            ftp.login(r.username or "anonymous", r.password or "")
        ftp.set_pasv(True)
        return ftp
    except ftplib.error_perm as e:
        raise DeliveryError(f"The server rejected the login ({e}).")
    except (socket.gaierror, socket.timeout, OSError) as e:
        raise DeliveryError(f"Could not reach {r.host}:{r.port or 21} ({e}).")


def test_connection(r: m.Recipient) -> str:
    """Connect, change to the remote folder, list it. Returns a one-line human summary; raises DeliveryError."""
    if r.delivery == "download":
        return "This recipient is set to manual download, so there is nothing to connect to."
    folder = r.remote_dir or "."
    if r.delivery == "sftp":
        transport, sftp = _sftp_client(r)
        try:
            try:
                names = sftp.listdir(folder)
            except IOError:
                raise DeliveryError(f"Connected, but the folder '{folder}' does not exist or is not readable.")
            return f"Connected to {r.host} over SFTP. Folder '{folder}' is reachable and holds {len(names)} item(s)."
        finally:
            sftp.close(); transport.close()
    ftp = _ftp_client(r)
    try:
        try:
            ftp.cwd(folder)
        except ftplib.error_perm:
            raise DeliveryError(f"Connected, but the folder '{folder}' does not exist or is not readable.")
        names = ftp.nlst()
        return f"Connected to {r.host} over {r.delivery.upper()}. Folder '{folder}' is reachable and holds {len(names)} item(s)."
    finally:
        try:
            ftp.quit()
        except Exception:
            pass


def deliver(r: m.Recipient, local_path: Path) -> str:
    """Upload one file. Returns the remote path. Writes to a temporary name first, then renames, so a
    recipient's poller never picks up a half-written file."""
    if r.delivery == "download":
        raise DeliveryError("This recipient is set to manual download.")
    folder = (r.remote_dir or ".").rstrip("/") or "/"
    final = posixpath.join(folder, local_path.name)
    tmp = posixpath.join(folder, local_path.name + ".part")
    if r.delivery == "sftp":
        transport, sftp = _sftp_client(r)
        try:
            sftp.put(str(local_path), tmp)
            try:
                sftp.remove(final)
            except IOError:
                pass
            sftp.rename(tmp, final)
        except IOError as e:
            raise DeliveryError(f"Upload failed: {e}")
        finally:
            sftp.close(); transport.close()
        return final
    ftp = _ftp_client(r)
    try:
        with open(local_path, "rb") as fh:
            ftp.storbinary(f"STOR {tmp}", fh)
        try:
            ftp.delete(final)
        except ftplib.error_perm:
            pass
        ftp.rename(tmp, final)
    except ftplib.all_errors as e:
        raise DeliveryError(f"Upload failed: {e}")
    finally:
        try:
            ftp.quit()
        except Exception:
            pass
    return final
