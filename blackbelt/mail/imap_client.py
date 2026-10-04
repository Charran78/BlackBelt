"""Read-only Gmail IMAP access and bounded MIME extraction."""

from __future__ import annotations

import imaplib
import re
import ssl
from datetime import datetime, timedelta, timezone
from email import message_from_bytes
from email.header import decode_header, make_header
from email.message import Message
from email.policy import default
from email.utils import parseaddr
from html.parser import HTMLParser
from typing import Callable, Sequence
from urllib.parse import urlsplit

from blackbelt.mail.models import MailMessage
from blackbelt.mail.settings import EmailSettings


class MailFetchError(RuntimeError):
    """Raised when Gmail cannot be queried or a message cannot be fetched."""


class _PlainTextHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_parts: list[str] = []
        self.link_domains: set[str] = set()
        self._hidden_depth = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag in {"script", "style"}:
            self._hidden_depth += 1
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                try:
                    domain = urlsplit(href).hostname
                except ValueError:
                    return
                if domain:
                    self.link_domains.add(domain.casefold().rstrip("."))

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._hidden_depth:
            self._hidden_depth -= 1
        if tag in {"p", "div", "br", "li", "tr"}:
            self.text_parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._hidden_depth:
            self.text_parts.append(data)


def fetch_recent_messages(
    username: str,
    app_password: str,
    settings: EmailSettings,
    *,
    imap_factory: Callable[..., imaplib.IMAP4_SSL] | None = None,
) -> list[MailMessage]:
    """Fetch a bounded recent window without marking messages as read."""
    factory = imap_factory or imaplib.IMAP4_SSL
    connection: imaplib.IMAP4_SSL | None = None
    try:
        connection = factory(
            "imap.gmail.com",
            timeout=settings.imap_timeout,
            ssl_context=ssl.create_default_context(),
        )
        connection.login(username, app_password)
        status, _ = connection.select("INBOX", readonly=True)
        if status != "OK":
            raise MailFetchError("No se pudo abrir la bandeja de entrada.")

        window_start = datetime.now(timezone.utc) - timedelta(hours=settings.hours)
        since_argument = window_start.strftime("%d-%b-%Y")
        status, search_data = connection.uid(
            "search",
            None,
            "SINCE",
            since_argument,
        )
        if status != "OK":
            raise MailFetchError("Gmail no pudo buscar los mensajes recientes.")
        identifiers = search_data[0].split() if search_data else []
        selected = identifiers[-settings.message_limit:][::-1]

        messages: list[MailMessage] = []
        for identifier in selected:
            status, response = connection.uid(
                "fetch",
                identifier,
                f"(INTERNALDATE BODY.PEEK[]<0.{settings.max_message_bytes}>)",
            )
            if status != "OK":
                continue
            internal_date = _message_internal_date(response)
            if internal_date is None or internal_date < window_start:
                continue
            raw_message = _message_bytes(response)
            if raw_message is None:
                continue
            messages.append(
                parse_message(
                    raw_message,
                    identifier.decode("ascii", errors="replace"),
                    settings.max_body_chars,
                )
            )
        return messages
    except MailFetchError:
        raise
    except (imaplib.IMAP4.error, OSError, ssl.SSLError, TimeoutError) as exc:
        raise MailFetchError(
            "Falló la conexión IMAP con Gmail. Revisa la red, IMAP habilitado "
            "y la contraseña de aplicación."
        ) from exc
    finally:
        if connection is not None:
            try:
                connection.logout()
            except (imaplib.IMAP4.error, OSError):
                pass


def parse_message(raw_message: bytes, uid: str, max_body_chars: int) -> MailMessage:
    message = message_from_bytes(raw_message, policy=default)
    sender = _decode_header(str(message.get("From", "")))
    subject = _decode_header(str(message.get("Subject", "(Sin asunto)")))
    message_id = _decode_header(
        str(message.get("Message-ID", uid))
    ).strip() or uid
    body, link_domains = _extract_body(message, max_body_chars)
    signals = _analyze_signals(message, sender, link_domains)
    return MailMessage(
        uid=uid,
        message_id=message_id,
        sender=sender,
        subject=subject,
        body=body,
        signals=signals,
    )


def _message_bytes(response: Sequence[object] | None) -> bytes | None:
    if response is None:
        return None
    for item in response:
        if (
            isinstance(item, tuple)
            and len(item) >= 2
            and isinstance(item[1], bytes)
        ):
            return item[1]
    return None


def _message_internal_date(
    response: Sequence[object] | None,
) -> datetime | None:
    if response is None:
        return None
    for item in response:
        if not isinstance(item, tuple) or not item or not isinstance(item[0], bytes):
            continue
        match = re.search(rb'\bINTERNALDATE\s+"([^"]+)"', item[0])
        if not match:
            continue
        value = match.group(1).decode("ascii", errors="replace")
        try:
            parsed = datetime.strptime(value, "%d-%b-%Y %H:%M:%S %z")
        except ValueError:
            return None
        return parsed.astimezone(timezone.utc)
    return None


def _decode_header(value: str) -> str:
    try:
        return str(make_header(decode_header(value)))
    except (LookupError, UnicodeDecodeError):
        return value


def _extract_body(message: Message, max_chars: int) -> tuple[str, set[str]]:
    plain_parts: list[str] = []
    html_parts: list[str] = []
    link_domains: set[str] = set()
    parts = message.walk() if message.is_multipart() else (message,)
    for part in parts:
        if part.get_content_disposition() == "attachment":
            continue
        content_type = part.get_content_type()
        if content_type not in {"text/plain", "text/html"}:
            continue
        content = _decode_part(part)
        if not content:
            continue
        if content_type == "text/plain":
            plain_parts.append(content)
        else:
            parser = _PlainTextHTMLParser()
            parser.feed(content)
            html_parts.append(" ".join(parser.text_parts))
            link_domains.update(parser.link_domains)
        if sum(map(len, plain_parts)) >= max_chars:
            break

    text = "\n".join(plain_parts)
    if not text:
        text = "\n".join(html_parts)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars], link_domains


def _decode_part(part: Message) -> str:
    try:
        content = part.get_content()
    except (LookupError, UnicodeDecodeError, TypeError):
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes):
            return ""
        charset = part.get_content_charset() or "utf-8"
        try:
            return payload.decode(charset, errors="replace")
        except LookupError:
            return payload.decode("utf-8", errors="replace")
    return content if isinstance(content, str) else ""


def _analyze_signals(
    message: Message,
    sender: str,
    link_domains: set[str],
) -> dict[str, str | bool]:
    authentication = _gmail_authentication_results(message)
    signals: dict[str, str | bool] = {
        name: _authentication_result(authentication, name)
        for name in ("spf", "dkim", "dmarc")
    }

    sender_address = parseaddr(sender)[1].casefold()
    sender_domain = sender_address.rpartition("@")[2]
    reply_to = parseaddr(str(message.get("Reply-To", "")))[1].casefold()
    signals["sender_domain"] = sender_domain or "desconocido"
    signals["reply_to_differs"] = bool(
        reply_to and sender_address and reply_to != sender_address
    )
    signals["link_domain_mismatch"] = bool(
        link_domains
        and sender_domain
        and not any(
            domain == sender_domain or domain.endswith("." + sender_domain)
            for domain in link_domains
        )
    )
    return signals


def _gmail_authentication_results(message: Message) -> str:
    trusted_headers = []
    for value in message.get_all("Authentication-Results", []):
        header = str(value)
        authserv_id = header.partition(";")[0].strip().casefold()
        if authserv_id == "mx.google.com":
            trusted_headers.append(header.casefold())
    return trusted_headers[0] if len(trusted_headers) == 1 else ""


def _authentication_result(authentication: str, mechanism: str) -> str:
    match = re.search(
        rf"\b{mechanism}\s*=\s*(pass|fail|softfail|neutral)\b",
        authentication,
    )
    return match.group(1) if match else "unknown"
