"""Local incident cache and desktop notification helper."""
import contextlib
import datetime as dt
import fcntl
import http.client
import ipaddress
import json
import os
from pathlib import Path
from queue import Empty, Queue
import re
import socket
import ssl
import subprocess
import sys
import tempfile
from threading import Thread
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4
import xml.etree.ElementTree as ET

SOURCES = {
    "openai": ("OpenAI / ChatGPT", "rss", "https://status.openai.com/feed.rss", "https://status.openai.com/"),
    "claude": ("Claude", "summary", "https://status.claude.com/api/v2/incidents.json", "https://status.claude.com/"),
    "grok": ("Grok Bot", "rss", "https://status.x.ai/feed.xml", "https://status.x.ai/grok-bot"),
    "router": ("OpenRouter", "rss", "https://status.openrouter.ai/history.rss", "https://status.openrouter.ai/"),
    "github": ("GitHub", "summary", "https://www.githubstatus.com/api/v2/incidents.json", "https://www.githubstatus.com/"),
    "cloudflare": ("Cloudflare", "summary", "https://www.cloudflarestatus.com/api/v2/incidents.json", "https://www.cloudflarestatus.com/"),
    "aws": ("AWS public notices", "notices", "https://status.aws.amazon.com/rss/all.rss", "https://health.aws.amazon.com/health/status"),
    "gcp": ("Google Cloud", "google", "https://status.cloud.google.com/incidents.json", "https://status.cloud.google.com/"),
    "workspace": ("Google Workspace", "google", "https://www.google.com/appsstatus/dashboard/incidents.json", "https://www.google.com/appsstatus/dashboard/"),
    "discord": ("Discord", "summary", "https://discordstatus.com/api/v2/incidents.json", "https://discordstatus.com/"),
    "reddit": ("Reddit", "summary", "https://www.redditstatus.com/api/v2/incidents.json", "https://www.redditstatus.com/"),
    "twitch": ("Twitch", "summary", "https://status.twitch.com/api/v2/incidents.json", "https://status.twitch.com/"),
    "zoom": ("Zoom", "summary", "https://www.zoomstatus.com/api/v2/incidents.json", "https://www.zoomstatus.com/"),
    "slack": ("Slack", "slack", "https://slack-status.com/api/v2.0.0/history", "https://slack-status.com/"),
    "azure": ("Azure public notices", "notices", "https://rssfeed.azure.status.microsoft/en-us/status/feed/", "https://azure.status.microsoft/en-us/status/"),
    "epic": ("Epic Games / Fortnite", "custom-statuspage", "https://status.epicgames.com/api/v2/incidents.json", "https://status.epicgames.com/"),
    "cash-app": ("Cash App", "custom-statuspage", "https://status.cash.app/api/v2/incidents.json", "https://status.cash.app/"),
    "se-swish": ("Swish", "custom-statuspage", "https://status.swish.nu/api/v2/incidents.json", "https://status.swish.nu/"),
}
SOURCE_COUNTRIES = {"cash-app": "US", "se-swish": "SE"}
COUNTRIES = {"global": "Other / global", **json.loads(
    Path(__file__).with_name("countries.json").read_text(encoding="utf-8"))}
# ID -> country, name, kind of check, provider page, provider alert help (optional).
LINKS = {
    "xfinity": ("US", "Xfinity", "Home broadband · address lookup", "https://www.xfinity.com/support/statusmap", "https://www.xfinity.com/support/articles/proactive-outage-notifications"),
    "tds": ("US", "TDS Telecom", "Home broadband · address lookup", "https://tdstelecom.com/support/outage.html", "https://tdstelecom.com/support/outage.html"),
    "att": ("US", "AT&T Internet / Fiber", "Home broadband · address lookup", "https://www.att.com/outages/"),
    "spectrum": ("US", "Spectrum", "Home broadband · provider account", "https://www.spectrum.net/support/troubleshooting-your-spectrum-services-after-outage/"),
    "cox": ("US", "Cox", "Home broadband · provider account", "https://www.cox.com/residential/support/outages.html"),
    "fios": ("US", "Verizon Fios / 5G Home", "Home broadband · network check", "https://www.verizon.com/support/check-network-status/"),
    "frontier": ("US", "Frontier", "Home broadband · address lookup", "https://frontier.com/helpcenter/internet/troubleshooting/check-for-service-outages"),
    "optimum": ("US", "Optimum", "Home broadband · outage map", "https://www.optimum.net/support/outage/"),
    "gfiber": ("US", "Google Fiber", "Home broadband · address lookup", "https://gfiber.com/outage/check", "https://www.prd.gfiber.com/support/en/answer/1952/"),
    "quantum": ("US", "Quantum Fiber", "Home broadband · provider account", "https://www.quantumfiber.com/support/speed-performance/outages"),
    "centurylink": ("US", "CenturyLink", "Home broadband · provider support", "https://www.centurylink.com/home/help/internet/internet-or-phone-not-working.html"),
    "tmobile-home": ("US", "T-Mobile Home Internet", "Home broadband · T-Life diagnostics", "https://www.t-mobile.com/support/home-internet/customer-support"),
    "verizon-mobile": ("US", "Verizon Wireless", "Mobile · network check", "https://www.verizon.com/support/check-network-status/", "https://www.verizon.com/support/network-outage-faqs/"),
    "tmobile": ("US", "T-Mobile Wireless", "Mobile · provider sign-in", "https://network.t-mobile.com/"),
    "att-mobile": ("US", "AT&T Wireless", "Mobile · local outage checker", "https://www.att.com/outages/"),
    "mte": ("US", "Middle Tennessee Electric", "Local power · outage map", "https://mte.com/outages", "https://mte.com/emergency-management-and-outage-alerts"),
    "chase": ("US", "Chase", "Online and mobile banking support", "https://www.chase.com/digital/customer-service"),
    "bank-of-america": ("US", "Bank of America", "Online banking support", "https://www.bankofamerica.com/customer-service/contact-us/online-banking/"),
    "citi": ("US", "Citibank", "Personal banking support", "https://online.citi.com/US/ag/contactus"),
    "wells-fargo": ("US", "Wells Fargo", "Online banking support", "https://www.wellsfargo.com/help/online-banking/"),
    "us-bank": ("US", "U.S. Bank", "Online and mobile banking support", "https://www.usbank.com/customer-service/personal-banking/mobile-and-online-banking.html"),
    "capital-one": ("US", "Capital One", "Account and sign-in support", "https://www.capitalone.com/help-center/"),
    "pnc": ("US", "PNC Bank", "Online and mobile banking support", "https://www.pnc.com/en/customer-service/online-mobile-customer-service.html"),
    "truist": ("US", "Truist", "Online and mobile banking support", "https://www.truist.com/contact/products-and-services-directory"),
    "td-us": ("US", "TD Bank", "US personal banking support", "https://www.td.com/us/en/personal-banking/help-center"),
    "venmo": ("US", "Venmo", "App error troubleshooting and support", "https://help.venmo.com/cs/articles/error-messages-vhel262"),
    "zelle": ("US", "Zelle", "Payment troubleshooting and support", "https://www.zellepay.com/contact-us"),
    "walmart-us": ("US", "Walmart", "Walmart.com orders, pickup and delivery support", "https://www.walmart.com/help"),
    "amazon-us": ("US", "Amazon Shopping", "Amazon.com orders and account support", "https://digprjsurvey.amazon.com/csad/help/node/GSD587LKW72HKU2V"),
    "bt": ("GB", "BT Broadband", "Home broadband · service checker", "https://www.bt.com/help/check-service-status"),
    "virgin": ("GB", "Virgin Media", "Home broadband · service checker", "https://www.virginmedia.com/help/service-status"),
    "sky": ("GB", "Sky", "Home broadband · service checker", "https://www.sky.com/help/servicestatus"),
    "ee": ("GB", "EE", "Mobile · network check", "https://myaccount.ee.co.uk/networkchecker/checkservice"),
    "o2": ("GB", "O2", "Mobile · network check", "https://status.o2.co.uk/"),
    "vodafone": ("GB", "Vodafone UK", "Mobile · network check", "https://www.vodafone.co.uk/network/status-checker"),
    "three": ("GB", "Three UK", "Mobile · network check", "https://www.three.co.uk/support/network-and-coverage/network-support"),
    "talktalk": ("GB", "TalkTalk", "Home broadband · service checker", "https://help-centre.talktalk.co.uk/Service_Status"),
    "plusnet": ("GB", "Plusnet", "Broadband troubleshooting and support", "https://www.plus.net/help/plusnet-broadband-help/"),
    "hyperoptic": ("GB", "Hyperoptic", "Home broadband · outage checker", "https://www.hyperoptic.com/support/internet-and-wi-fi/check-service-status-and-outages"),
    "lloyds": ("GB", "Lloyds Bank", "Online and mobile banking service status", "https://www.lloydsbank.com/help-guidance/service-status.html"),
    "nationwide": ("GB", "Nationwide", "Internet banking and app service status", "https://www.nationwide.co.uk/ways-to-bank/service-status"),
    "natwest": ("GB", "NatWest", "Online and mobile banking service status", "https://www.natwest.com/service-status.html"),
    "barclays": ("GB", "Barclays", "Online banking troubleshooting and support", "https://www.barclays.co.uk/help/ways-to-bank/online-banking/"),
    "uk-power-networks": ("GB", "UK Power Networks", "Local power · outage map", "https://www.ukpowernetworks.co.uk/power-cut/map"),
    "royal-mail": ("GB", "Royal Mail", "Postal and delivery service updates", "https://www.royalmail.com/service-update"),
    "amazon-gb": ("GB", "Amazon Shopping UK", "Amazon.co.uk orders and account support", "https://digprjsurvey.amazon.co.uk/csad/help/node/GSD587LKW72HKU2V"),
    "rogers": ("CA", "Rogers / Shaw", "Broadband and mobile · provider outage support", "https://www.rogers.com/support/outage"),
    "bell": ("CA", "Bell", "Broadband and mobile · service checker", "https://support.bell.ca/Outage-Check"),
    "telus": ("CA", "TELUS", "Broadband and mobile · service checker", "https://www.telus.com/outages", "https://www.telus.com/en/support/article/service-outage"),
    "videotron": ("CA", "Vidéotron", "Broadband and mobile · outage information", "https://www.videotron.com/en/support/outages"),
    "cogeco": ("CA", "Cogeco", "Home broadband · postal code lookup", "https://www.cogeco.ca/en/support/outages"),
    "koodo": ("CA", "Koodo Mobile", "Mobile network troubleshooting and support", "https://www.koodomobile.com/en/help/im-not-getting-any-network-service"),
    "rbc": ("CA", "RBC Royal Bank", "Online and mobile banking support", "https://www.rbcroyalbank.com/customer-service/"),
    "td-ca": ("CA", "TD Canada Trust", "EasyWeb and mobile banking support", "https://www.td.com/ca/en/personal-banking/contact-us"),
    "scotiabank": ("CA", "Scotiabank", "Personal and digital banking support", "https://www.scotiabank.com/ca/en/personal/contact-us.html"),
    "bmo": ("CA", "BMO Bank of Montreal", "Online and mobile banking support", "https://www.bmo.com/main/personal/contact-centre-support-tool/"),
    "cibc": ("CA", "CIBC", "Personal and digital banking support", "https://www.cibc.com/en/contact-us.html"),
    "interac": ("CA", "Interac e-Transfer", "Payment troubleshooting and support", "https://www.interac.ca/en/resources/personal-resources/personal-faq/interac-e-transfer/"),
    "bc-hydro": ("CA", "BC Hydro", "Local power · outage map", "https://www.bchydro.com/power-outages/app/outage-map.html"),
    "hydro-quebec": ("CA", "Hydro-Québec", "Local power · outage map", "https://infopannes.solutions.hydroquebec.com/info-pannes/pannes/pannes-en-cours?langue=en"),
    "hydro-one": ("CA", "Hydro One", "Local power · outage map", "https://stormcentre.hydroone.com/"),
    "walmart-ca": ("CA", "Walmart Canada", "Walmart.ca orders, pickup and delivery support", "https://www.walmart.ca/en/help"),
    "telstra": ("AU", "Telstra", "Broadband and mobile · outage checker", "https://www.telstra.com.au/outages"),
    "optus": ("AU", "Optus", "Broadband and mobile · service checker", "https://www.optus.com.au/living-network/service-status"),
    "nbn": ("AU", "nbn", "Fixed broadband network · address lookup", "https://www.nbnco.com.au/support/network-status"),
    "kpn": ("NL", "KPN", "Broadband and mobile · outage checker", "https://www.kpn.com/service/storingen"),
    "ziggo": ("NL", "Ziggo", "Home broadband · outage checker", "https://www.ziggo.nl/klantenservice/storingen"),
    "vodafone-nl": ("NL", "Vodafone NL", "Mobile · outage checker", "https://www.vodafone.nl/storingen"),
    "odido": ("NL", "Odido", "Broadband and mobile · outage checker", "https://www.odido.nl/storingen-en-onderhoud"),
    "delta": ("NL", "DELTA Fiber", "Home broadband · outage checker", "https://www.delta.nl/klantenservice/storingen/"),
    "freedom": ("NL", "Freedom Internet", "Home broadband · outage page", "https://www.freedom.nl/storingen"),
    "liander": ("NL", "Liander", "Local power and gas · outage map", "https://www.liander.nl/storingen-en-onderhoud"),
    "stedin": ("NL", "Stedin", "Local power and gas · outage map", "https://www.stedin.net/storingen"),
    "enexis": ("NL", "Enexis", "Local power and gas · outage map", "https://www.enexis.nl/storingen-en-onderhoud"),
    "br-cemig": ("BR", "Cemig", "Minas Gerais · scheduled power cuts", "https://www.cemig.com.br/desligamento-programado/"),
    "br-claro": ("BR", "Claro Brasil", "Home internet and TV · Minha Claro diagnostics", "https://www.claro.com.br/atendimento/suporte-tecnico-residencial"),
    "br-enel-distribuicao": ("BR", "Enel Distribuição Brasil", "Regional electricity · customer outage lookup", "https://www.enel.com.br/pt/servico/consultar-falta-de-luz.html"),
    "br-nubank": ("BR", "Nubank", "Mobile banking · app disruption guidance", "https://blog.nubank.com.br/aplicativo-nubank-fora-do-ar-esta-tudo-bem-saiba-o-que-fazer/"),
    "de-bayernwerk-netz": ("DE", "Bayernwerk Netz", "Bavaria · electricity outage map", "https://www.bayernwerk-netz.de/de/bayernwerk-netz-gmbh/presse-kontakt/stoerungsauskunft.html"),
    "de-deutsche-telekom": ("DE", "Deutsche Telekom", "Home internet and phone · line and fault status", "https://www.telekom.de/hilfe/hilfe-bei-stoerungen/uebersicht-status"),
    "de-dhl-paket": ("DE", "DHL Paket Deutschland", "Parcel delivery · tracking issues and delay guidance", "https://www.dhl.de/de/privatkunden/hilfe-kundenservice/sendungsverfolgung/probleme-loesungen.html"),
    "de-ing-deutschland": ("DE", "ING Deutschland", "Online and mobile banking · scheduled maintenance notices", "https://www.ing.de/wissen/releases-downtimes/"),
    "de-o2": ("DE", "O2 Germany", "Mobile network · address Live-Check", "https://www.o2online.de/netz/netzstoerung/"),
    "de-vodafone": ("DE", "Vodafone Deutschland", "Cable, DSL, fiber and mobile · address outage finder", "https://www.vodafone.de/hilfe/stoerung.html"),
    "es-edistribucion": ("ES", "e-distribución", "Electricity distribution · live outage map and planned works", "https://www.edistribucion.com/es/averias.html", "https://www.edistribucion.com/content/edistribucion/es/servicios/cortes-averias/suscripcion-cortes-averias.html"),
    "es-ide-iberdrola": ("ES", "i-DE (Grupo Iberdrola)", "Regional electricity · town or address lookup", "https://www.i-de.es/averias-cortes-luz/mapa-averias"),
    "es-movistar": ("ES", "Movistar España", "Fiber, mobile and TV · fault diagnostics and ticket status", "https://www.movistar.es/atencion-cliente/averias-soporte-tecnico"),
    "es-orange": ("ES", "Orange Spain", "Home internet · connection troubleshooting", "https://ayuda.orange.es/particulares/internet-y-wi-fi/configuracion-e-instalacion/6870-problemas-de-conexion-en-casa-soluciones-rapidas-antes-de-llamar-al-tecnico"),
    "es-vodafone": ("ES", "Vodafone Spain", "Home internet · connection troubleshooting", "https://ayudacliente.vodafone.es/particulares/internet-y-fijo/wifi-y-router/que-pasos-seguir-cuando-no-tienes-internet/"),
    "fr-bouygues-telecom": ("FR", "Bouygues Telecom", "Mobile network · address quality and incident lookup", "https://www.assistance.bouyguestelecom.fr/mobile-incident-panne-reseau"),
    "fr-enedis": ("FR", "Enedis", "Electricity distribution · address power cut checker", "https://www.enedis.fr/panne-et-interruption"),
    "fr-grdf": ("FR", "GRDF", "Natural gas distribution · InfoCoupure address checker", "https://infocoupure.grdf.fr/icfo-gpublic/"),
    "fr-la-poste": ("FR", "La Poste", "Postal and parcel delivery · service disruption notices", "https://aide.laposte.fr/kb/guide/fr/conditions-meteorologiques-et-autres-perturbations-impacts-sur-la-distribution-postale-N1pZI5MFvb"),
    "fr-orange": ("FR", "Orange France", "Home internet and phone · local network checker guide", "https://assistance.orange.fr/article/infos-reseau-internet-et-fixe-presentation_303052"),
    "fr-sfr": ("FR", "SFR", "Mobile network · public cell site outage and maintenance table", "https://assistance.sfr.fr/carte-incident-reseau-mobile.html"),
    "ie-eir": ("IE", "eir", "Broadband and landline · account line fault checker", "https://eir.ie/network-status"),
    "ie-esb-networks": ("IE", "ESB Networks", "Electricity network · live PowerCheck outage map", "https://powercheck.esbnetworks.ie/", "https://www.esbnetworks.ie/services/power-outages/Get-planned-outage-updates"),
    "ie-three": ("IE", "Three Ireland", "Mobile and broadband · county outage and planned work list", "https://www.three.ie/support/outages-planned-work.html"),
    "ie-uisce-eireann": ("IE", "Uisce Éireann", "Water supply · local disruption map", "https://www.water.ie/in-your-area?map=supply-and-service-updates", "https://www.water.ie/water-service-updates"),
    "ie-virgin-media": ("IE", "Virgin Media Ireland", "Home broadband · account-number line check", "https://www.virginmedia.ie/customer-support/support-by-topic/service-status/"),
    "ie-vodafone": ("IE", "Vodafone Ireland", "Broadband, mobile and TV · live service updates", "https://www.vodafone.ie/service-updates"),
    "in-delhi-power": ("IN", "Delhi Power Department", "Delhi · planned power outage reports", "https://power.delhi.gov.in/power/daily-power-outage-reportsplanned"),
    "in-jio": ("IN", "Jio", "JioFiber broadband · MyJio network diagnostics guide", "https://www.jio.com/help/helpful-tips/fiber/run-diagnostics/"),
    "in-tata-power-mumbai": ("IN", "Tata Power", "Mumbai · current and planned power outages", "https://customerportal.tatapower.com/TPCD/OuterOutage.aspx"),
    "it-areti": ("IT", "Areti", "Rome · electricity outage map", "https://www.areti.it/assistenza/interruzioni-e-guasti-rete-elettrica"),
    "it-e-distribuzione": ("IT", "E-Distribuzione", "Electricity distribution · outage map and supply code lookup", "https://www.e-distribuzione.it/interruzione-corrente-primo.html"),
    "it-iliad": ("IT", "iliad Italia", "Home fibre · iliadbox connection troubleshooting", "https://assistenza.iliad.it/privati/fibra/supporto/risoluzione-problemi-iliadbox"),
    "it-tim": ("IT", "TIM", "Home internet · diagnose or report a fault", "https://www.tim.it/assistenza/assistenza-tecnica/servizi-online/apri-segnalazione"),
    "jp-kddi-au": ("JP", "KDDI / au", "Mobile and au Hikari fiber · outage and maintenance notices", "https://www.kddi.com/personal/notice/"),
    "jp-kepco-td": ("JP", "Kansai Transmission and Distribution", "Kansai · electricity outage bulletin", "https://www.kansai-td.co.jp/teiden-info/index.php?ScreenName=MP00"),
    "jp-ntt-docomo": ("JP", "NTT DOCOMO", "Mobile network · Japanese service outage status board", "https://www.docomo.ne.jp/info/service_status/"),
    "jp-ntt-east": ("JP", "NTT East", "FLET'S Hikari fiber and landline · outage and maintenance notices", "https://www.ntt-east.co.jp/disclosure/construction.html"),
    "jp-paypay": ("JP", "PayPay", "Mobile QR payment app · official outage notices", "https://paypay.ne.jp/notice/obstacle/"),
    "jp-softbank": ("JP", "SoftBank", "Mobile network · service outage and recovery notices", "https://www.softbank.jp/mobile/info/personal/important/"),
    "jp-tepco-pg": ("JP", "TEPCO Power Grid", "Tokyo and Kanto · electricity outage map", "https://teideninfo.tepco.co.jp/"),
    "jp-yamato-transport": ("JP", "Yamato Transport", "TA-Q-BIN parcel delivery · regional delay and suspension tracker", "https://www.kuronekoyamato.co.jp/ytc/chien/chien_hp.html"),
    "ch-bkw": ("CH", "BKW", "Regional electricity · outage map and fault information", "https://strom.bkw.com/de/priv/dienstleistungen-fuer-haushalte/stoerungen-und-unterbrueche"),
    "ch-salt": ("CH", "Salt", "Mobile and home internet · service interruptions", "https://www.salt.ch/de/mobile/network-and-service-interruptions"),
    "ch-sunrise": ("CH", "Sunrise", "Telecoms · current service notices", "https://www.sunrise.ch/en/support/current-malfunctions"),
    "ch-swisscom": ("CH", "Swisscom", "Home internet and mobile · network status", "https://www.swisscom.ch/en/residential/help/network-and-service-status.html"),
    "id-gopay": ("ID", "GoPay", "Mobile payments · transaction and app troubleshooting", "https://gopay.co.id/bantuan/pengaturan-akun-aplikasi/saya-tidak-dapat-menggunakan-gopay"),
    "id-indihome": ("ID", "IndiHome", "Home broadband · connection troubleshooting", "https://www.telkomsel.com/support/koneksi-indihome-bermasalah"),
    "id-myrepublic": ("ID", "MyRepublic Indonesia", "Home broadband · check connection and modem", "https://www.myrepublic.co.id/blog/cara-cek-jaringan-dan-restart-modem-mandiri"),
    "id-pln": ("ID", "PLN", "Electricity · report or follow faults using PLN Mobile", "https://layanan.pln.co.id/pln-mobile"),
    "kr-kt": ("KR", "KT", "Home internet and TV · fault diagnostics", "https://help.kt.com/asreq/HomeAsReqStatusInfo.do"),
    "kr-kt-notices": ("KR", "KT Network Notices", "Telecoms · service interruptions and maintenance notices", "https://inside.kt.com/html/notice/net_notice_list.html"),
    "kr-lg-uplus": ("KR", "LG U+", "Telecoms · network disruption notices", "https://www.lguplus.com/footer/issue-status"),
    "kr-sk-broadband": ("KR", "SK Broadband", "Home internet · connection troubleshooting", "https://blog.bworld.co.kr/2026/11/14/internet-disconnect-self-check-before-as/"),
    "mx-cfe": ("MX", "CFE", "Electricity · how to report a supply fault", "https://ventanilla.energia.gob.mx/detalle_tramite/MzU5"),
    "mx-izzi": ("MX", "izzi", "Home internet and TV · fault reporting and support", "https://ayudaizzi.izzi.mx/hc/es/articles/10615665360154-Reporta-fallas-de-tu-servicio-izzi"),
    "mx-telmex": ("MX", "Telmex", "Home broadband · sign in to report a connection fault", "https://mitelmex.telmex.com/web/mitelmex-hogar/reporte-internet"),
    "nz-2degrees": ("NZ", "2degrees", "Mobile and broadband · network status", "https://www.2degrees.nz/network-status"),
    "nz-one": ("NZ", "One NZ", "Mobile and home internet · network outages", "https://one.nz/help/network-status/"),
    "nz-spark": ("NZ", "Spark NZ", "Mobile and broadband · network outages and updates", "https://www.spark.co.nz/online/help/outages"),
    "nz-vector": ("NZ", "Vector", "Auckland · electricity outage map", "https://help.vector.co.nz/map/1000"),
    "ph-gcash": ("PH", "GCash", "Mobile payments · service advisories and maintenance", "https://help.gcash.com/hc/en-us/p/service-status"),
    "ph-globe": ("PH", "Globe", "Mobile and broadband · network troubleshooting", "https://www.globe.com.ph/help/troubleshooting"),
    "ph-meralco": ("PH", "Meralco", "Greater Manila and nearby areas · outages and planned works", "https://www.meralco.com.ph/residential/outages-center"),
    "ph-pldt": ("PH", "PLDT Home", "Home internet · line status and repair ticket lookup", "https://status.pldthome.com/"),
    "pl-energa": ("PL", "Energa-Operator", "Regional electricity · current outages", "https://energa-operator.pl/uslugi/awarie-i-wylaczenia/wylaczenia-biezace", "https://energa-operator.pl/aktualnosci/862015/energa-operator-wprowadza-bezplatne-alerty-o-wylaczeniach"),
    "pl-orange": ("PL", "Orange Polska", "Mobile internet · network and fault troubleshooting", "https://www.orange.pl/view/pomoc-techniczna-internet-mobilny"),
    "pl-tauron": ("PL", "TAURON Dystrybucja", "Regional electricity · current and planned interruptions", "https://www.tauron-dystrybucja.pl/wylaczenia"),
    "se-ellevio": ("SE", "Ellevio", "Regional electricity · current and planned outages", "https://avbrottskarta.ellevio.se/"),
    "se-tele2": ("SE", "Tele2", "Home broadband · status and troubleshooting", "https://www.tele2.se/support/drift-och-felsok?kategori=bredband"),
    "se-telia": ("SE", "Telia Sweden", "Fixed network · service interruptions", "https://www.telia.se/support/driftinformation-fast"),
    "se-vattenfall": ("SE", "Vattenfall Eldistribution", "Regional electricity · active outage map", "https://www.vattenfalleldistribution.se/stromavbrott/pagaende-stromavbrott/"),
    "tr-ayedas": ("TR", "AYEDAŞ", "Asian Istanbul · electricity outage lookup", "https://online.ayedas.com.tr/elektrik-kesintisi-sorgulama"),
    "tr-turk-telekom": ("TR", "Türk Telekom", "Home internet · line fault reporting and status", "https://onlineislemler.turktelekom.com.tr/boim/bohm-misafir-ariza-islemleri-ve-ariza-sorgulama.jsp"),
    "tr-turknet": ("TR", "Turknet", "Home broadband · provider diagnostics", "https://www.turk.net/dijital-onarim-hizmeti"),
    "za-cape-town-electricity": ("ZA", "City of Cape Town Electricity", "Cape Town · load-shedding schedules and outage information", "https://www.capetown.gov.za/family-and-home/residential-utility-services/residential-electricity-services/load-shedding-and-outages/"),
    "za-eskom": ("ZA", "Eskom", "Electricity · report faults and check a report", "https://www.eskom.co.za/distribution/alfred-chat-bot/"),
    "za-openserve": ("ZA", "Openserve", "Fibre · line fault enquiry", "https://connect.openserve.co.za/pages/fibre-line-connection-fault-enquiry"),
    "za-vuma": ("ZA", "Vuma", "Fibre network · outages and maintenance", "https://vumatel.co.za/network-status"),
    "starlink": ("global", "Starlink", "Satellite broadband · Starlink app alerts", "https://starlink.com/us/support/article/5483745b-f8f0-d9c4-4d57-17c35a7c233a"),
    "instagram": ("global", "Instagram", "Consumer reports", "https://downdetector.com/status/instagram/"),
    "facebook": ("global", "Facebook", "Consumer reports", "https://downdetector.com/status/facebook/"),
    "youtube": ("global", "YouTube", "Official known issues", "https://support.google.com/youtube/threads?thread_filter=%28purpose%3Aknown_issue%29"),
    "outlook": ("global", "Outlook.com", "Microsoft consumer status", "https://status.cloud.microsoft/"),
    "m365": ("global", "Microsoft Teams / Microsoft 365", "Microsoft service health", "https://status.cloud.microsoft/m365"),
    "radar": ("global", "Cloudflare Radar", "Regional and network disruptions", "https://radar.cloudflare.com/outage-center"),
    "steam": ("global", "Steam", "Unofficial Steam network dashboard", "https://steamstat.us/"),
    "playstation": ("global", "PlayStation Network", "PlayStation service status", "https://status.playstation.com/"),
    "xbox": ("global", "Xbox Network", "Xbox service status", "https://support.xbox.com/xbox-live-status"),
    "roblox": ("global", "Roblox", "Roblox service status", "https://status.roblox.com/"),
    "nintendo": ("global", "Nintendo Switch Online", "Network maintenance and operational status", "https://www.nintendo.com/en-gb/Support/Online-Connection/Network-Maintenance-Information/Network-Maintenance-Information-241479.html"),
    "paypal": ("global", "PayPal", "PayPal production service status", "https://status.paypal.com/"),
}
CATEGORIES = {
    "Gaming": ("epic", "steam", "playstation", "xbox", "roblox", "nintendo"),
    "Banking": ("chase", "bank-of-america", "citi", "wells-fargo", "us-bank", "capital-one",
                "pnc", "truist", "td-us", "lloyds", "nationwide", "natwest", "barclays",
                "rbc", "td-ca", "scotiabank", "bmo", "cibc", "br-nubank", "de-ing-deutschland"),
    "Payments": ("paypal", "venmo", "zelle", "cash-app", "interac", "jp-paypay", "id-gopay", "ph-gcash", "se-swish"),
    "Shopping & delivery": ("amazon-us", "amazon-gb", "walmart-us", "walmart-ca", "royal-mail",
                            "de-dhl-paket", "fr-la-poste", "jp-yamato-transport"),
    "Internet & mobile": ("starlink", "radar"),
    "Power & utilities": ("mte", "uk-power-networks", "bc-hydro", "hydro-quebec", "hydro-one", "liander", "stedin", "enexis",
                          "br-cemig", "br-enel-distribuicao", "de-bayernwerk-netz", "es-edistribucion",
                          "es-ide-iberdrola", "fr-enedis", "fr-grdf", "ie-esb-networks", "ie-uisce-eireann",
                          "in-delhi-power", "in-tata-power-mumbai", "it-areti", "it-e-distribuzione",
                          "jp-kepco-td", "jp-tepco-pg",
                          "ch-bkw", "id-pln", "mx-cfe", "nz-vector",
                          "ph-meralco", "pl-energa", "pl-tauron", "se-ellevio",
                          "se-vattenfall", "tr-ayedas", "za-cape-town-electricity", "za-eskom"),
    "Apps & cloud": (),
    "Custom services": (),
}
CATEGORY_BY_ID = {key: name for name, keys in CATEGORIES.items() for key in keys}
SEARCH_ALIASES = {
    "ch-bkw": ("BKW Stromausfall",),
    "id-gopay": ("Gojek GoPay",),
    "id-indihome": ("Telkomsel IndiHome",),
    "id-myrepublic": ("MyRep Air",),
    "id-pln": ("PLN Mobile", "Perusahaan Listrik Negara"),
    "kr-kt": ("올레", "케이티"),
    "kr-kt-notices": ("KT 통신서비스",),
    "kr-lg-uplus": ("LG유플러스",),
    "kr-sk-broadband": ("SK브로드밴드", "B tv"),
    "mx-cfe": ("Comisión Federal de Electricidad", "CFE Contigo"),
    "mx-izzi": ("izzi telecom",),
    "mx-telmex": ("Infinitum", "Mi Telmex"),
    "nz-2degrees": ("Two Degrees",),
    "nz-one": ("Vodafone New Zealand", "Vodafone NZ"),
    "ph-globe": ("Globe Telecom", "GlobeOne"),
    "ph-meralco": ("Manila Electric Company", "Brownouts"),
    "ph-pldt": ("PLDTracker", "PLDT Fiber"),
    "pl-energa": ("Energa",),
    "pl-orange": ("Orange Poland", "Mój Orange"),
    "pl-tauron": ("Tauron",),
    "se-telia": ("Telia Sverige",),
    "se-vattenfall": ("Vattenfall",),
    "tr-ayedas": ("AYEDAS", "Anadolu Yakası Elektrik Dağıtım"),
    "tr-turk-telekom": ("Turk Telekom", "TTNET"),
    "tr-turknet": ("TürkNet",),
    "za-cape-town-electricity": ("City of Cape Town", "Load shedding"),
    "za-eskom": ("Alfred", "Eskom Distribution"),
    "za-openserve": ("Openserve Connect",),
    "za-vuma": ("Vumatel",),
    "br-cemig": ("Companhia Energética de Minas Gerais",),
    "br-claro": ("Claro Fibra", "Minha Claro"),
    "br-enel-distribuicao": ("Enel Brasil", "Enel SP", "Enel RJ", "Enel CE"),
    "br-nubank": ("Nu",),
    "de-bayernwerk-netz": ("Bayernwerk", "Störungsauskunft Bayernwerk"),
    "de-deutsche-telekom": ("Telekom", "T-Mobile DE"),
    "de-dhl-paket": ("DHL", "Deutsche Post DHL"),
    "de-ing-deutschland": ("ING", "ING-DiBa"),
    "de-o2": ("o2", "Telefónica Germany"),
    "de-vodafone": ("Vodafone DE", "Kabel Deutschland", "Unitymedia"),
    "es-edistribucion": ("Endesa Distribución", "EDISTRIBUCIÓN"),
    "es-ide-iberdrola": ("Iberdrola Distribución", "i-DE"),
    "es-movistar": ("Movistar", "Telefónica España"),
    "es-orange": ("Orange España",),
    "es-vodafone": ("Vodafone España",),
    "fr-bouygues-telecom": ("Bouygues", "Bbox"),
    "fr-enedis": ("ERDF",),
    "fr-grdf": ("Gaz Réseau Distribution France", "InfoCoupure"),
    "fr-la-poste": ("Colissimo", "Chronopost"),
    "fr-orange": ("Orange", "Sosh", "Météo du réseau Orange"),
    "fr-sfr": ("RED by SFR",),
    "ie-eir": ("eircom",),
    "ie-esb-networks": ("ESB", "PowerCheck"),
    "ie-three": ("3 Ireland",),
    "ie-uisce-eireann": ("Irish Water",),
    "ie-virgin-media": ("UPC Ireland",),
    "ie-vodafone": ("Vodafone IE",),
    "in-delhi-power": ("Tata Power-DDL", "TPDDL", "BSES"),
    "in-jio": ("Reliance Jio", "JioFiber"),
    "in-tata-power-mumbai": ("Tata Power Mumbai", "TPC"),
    "it-areti": ("Acea Areti",),
    "it-e-distribuzione": ("Enel Distribuzione",),
    "it-iliad": ("iliadbox",),
    "it-tim": ("Telecom Italia", "MyTIM"),
    "jp-kddi-au": ("au", "KDDI", "UQ mobile", "povo"),
    "jp-kepco-td": ("関西電力送配電", "KEPCO"),
    "jp-ntt-docomo": ("docomo", "NTTドコモ", "ahamo"),
    "jp-ntt-east": ("NTT東日本", "フレッツ光"),
    "jp-paypay": ("ペイペイ",),
    "jp-softbank": ("ソフトバンク", "Y!mobile", "LINEMO"),
    "jp-tepco-pg": ("東京電力パワーグリッド", "TEPCO"),
    "jp-yamato-transport": ("ヤマト運輸", "Kuroneko Yamato", "宅急便"),
    "xfinity": ("Comcast",),
    "spectrum": ("Charter Communications",),
    "cox": ("Cox Communications",),
    "att": ("ATT",),
    "att-mobile": ("ATT",),
    "mte": ("MTE",),
    "gcp": ("GCP",),
    "workspace": ("G Suite",),
    "m365": ("MS Teams", "Office 365"),
    "aws": ("Amazon Web Services",),
    "playstation": ("PSN",),
    "xbox": ("Xbox Live",),
    "steam": ("Steam Deck", "SteamDB", "Valve"),
    "walmart-us": ("Wallmart",),
    "walmart-ca": ("Wallmart",),
    "bank-of-america": ("BofA", "BoA"),
    "citi": ("Citi",),
    "chase": ("JPMorgan",),
    "us-bank": ("US Bank",),
}
LINK_LABELS = {
    "ch-salt": "Provider status page",
    "ch-sunrise": "Provider status page",
    "ch-swisscom": "Provider status page",
    "id-gopay": "Provider support",
    "id-indihome": "Provider support",
    "id-myrepublic": "Provider support",
    "id-pln": "Provider support",
    "kr-kt-notices": "Provider status page",
    "kr-lg-uplus": "Provider status page",
    "kr-sk-broadband": "Provider support",
    "mx-cfe": "Provider support",
    "mx-izzi": "Provider support",
    "mx-telmex": "Provider support",
    "nz-2degrees": "Provider status page",
    "nz-one": "Provider status page",
    "nz-spark": "Provider status page",
    "ph-gcash": "Provider status page",
    "ph-globe": "Provider support",
    "pl-orange": "Provider support",
    "tr-turk-telekom": "Provider support",
    "tr-turknet": "Provider support",
    "za-cape-town-electricity": "Provider support",
    "za-openserve": "Provider support",
    "za-vuma": "Provider status page",
    "br-cemig": "Provider maintenance notices",
    "br-claro": "Provider support",
    "br-nubank": "Provider support",
    "de-dhl-paket": "Provider support",
    "de-ing-deutschland": "Provider maintenance notices",
    "es-movistar": "Provider support",
    "es-orange": "Provider support",
    "es-vodafone": "Provider support",
    "fr-la-poste": "Provider known issues",
    "fr-orange": "Provider support",
    "fr-sfr": "Provider status page",
    "ie-three": "Provider status page",
    "ie-vodafone": "Provider status page",
    "in-delhi-power": "Official maintenance notices",
    "in-jio": "Provider support",
    "it-iliad": "Provider support",
    "it-tim": "Provider support",
    "jp-kddi-au": "Provider status page",
    "jp-kepco-td": "Provider status page",
    "jp-ntt-docomo": "Provider status page",
    "jp-ntt-east": "Provider status page",
    "jp-paypay": "Provider status page",
    "jp-softbank": "Provider status page",
    "jp-yamato-transport": "Provider status page",
    "starlink": "Provider support",
    "facebook": "Community outage reports",
    "instagram": "Community outage reports",
    "youtube": "Provider known issues",
    "outlook": "Provider status page",
    "m365": "Provider status page",
    "radar": "Network outage reports",
    "steam": "Community status dashboard",
    **dict.fromkeys(("playstation", "xbox", "roblox", "nintendo", "paypal", "lloyds", "nationwide",
                     "natwest", "royal-mail"), "Provider status page"),
}
DEFAULT_FAVOURITES = ("openai", "grok", "xfinity", "facebook", "youtube")
LIMIT = 4 * 1024 * 1024
STATE = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "omarchy-service-outages"

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

def fetch(url):
    opener = urllib.request.build_opener(NoRedirect())
    request = urllib.request.Request(url, headers={"User-Agent": "Omarchy-Service-Outages/0.1"})
    with opener.open(request, timeout=5) as response:
        data = response.read(LIMIT + 1)
    if len(data) > LIMIT:
        raise ValueError("Feed exceeds size limit")
    return data

def service_url(value):
    value = value.strip()
    try:
        url = urlsplit(value)
        if (len(value) > 2048 or re.search(r"[\s\\\x00-\x1f\x7f]", value)
                or url.scheme not in ("http", "https") or not url.hostname
                or url.username is not None or url.password is not None
                or url.netloc.endswith(":") or (url.port is not None and not 0 < url.port <= 65535)):
            raise ValueError
        host = url.hostname.encode("idna").decode("ascii")
        if ":" in host:
            host = f"[{ipaddress.IPv6Address(host)}]"
        elif not re.fullmatch(r"[a-z0-9.-]+", host):
            raise ValueError
        port = f":{url.port}" if url.port is not None else ""
        return urlunsplit((url.scheme, host + port, url.path or "/", url.query, url.fragment))
    except (ValueError, UnicodeError):
        raise ValueError("Enter a valid web address starting with https:// or http://.") from None

def statuspage_endpoint(url):
    page = urlsplit(url)
    if (page.scheme != "https" or page.port not in (None, 443) or page.query
            or page.path.rstrip("/") not in ("", "/history", "/api", "/api/v2/status.json", "/api/v2/summary.json",
                                             "/api/v2/incidents.json", "/api/v2/incidents/unresolved.json")):
        return None
    return urlunsplit(("https", page.netloc, "/api/v2/incidents.json", "", ""))

def fetch_custom_feed(url):
    """Use public HTTPS only, with no proxies, redirects or second DNS lookup."""
    endpoint = urlsplit(url)
    if statuspage_endpoint(url) != url:
        raise ValueError("Unsupported feed address")
    addresses = socket.getaddrinfo(endpoint.hostname, 443, type=socket.SOCK_STREAM)
    for _, _, _, _, sockaddr in addresses:
        address = ipaddress.ip_address(sockaddr[0])
        if (not address.is_global or address.is_multicast
                or (address.version == 6 and address not in ipaddress.ip_network("2000::/3"))):
            raise ValueError("Feed resolved to a non-public address")
    context = ssl.create_default_context()
    with contextlib.closing(http.client.HTTPSConnection(endpoint.hostname, context=context, timeout=4)) as conn:
        for family, socktype, protocol, _, sockaddr in addresses:
            with socket.socket(family, socktype, protocol) as transport:
                transport.settimeout(4)
                try:
                    transport.connect(sockaddr)
                except OSError:
                    continue
                conn.sock = context.wrap_socket(transport, server_hostname=endpoint.hostname)
                break
        if conn.sock is None:
            raise OSError("Could not connect to public feed")
        conn.request("GET", endpoint.path, headers={"User-Agent": "Omarchy-Canary", "Accept": "application/json"})
        with conn.getresponse() as response:
            if response.status != 200:
                raise ValueError(f"Feed returned HTTP {response.status}")
            body = response.read(LIMIT + 1)
    if len(body) > LIMIT:
        raise ValueError("Feed exceeds size limit")
    return body

def clean(value, limit=180):
    # Labels and notifications can safely be passed to controls that support markup.
    return " ".join(str(value or "").replace("<", "‹").replace(">", "›").replace("&", "＆").split())[:limit]

def incident(key, title, state, severity=0, scope=(), resolved_at=None):
    if not key or not title:
        raise ValueError("Incident is missing an ID or title")
    event = {"id": str(key)[:240], "title": clean(title), "state": state, "severity": severity,
             "scope": sorted({clean(x, 90) for x in scope if x})[:30]}
    if state == "resolved" and isinstance(resolved_at, str):
        try:
            timestamp = dt.datetime.fromisoformat(resolved_at)
            if timestamp.tzinfo is not None:
                event["resolvedAt"] = timestamp.timestamp()
        except (ValueError, OverflowError, OSError):
            pass
    return event

def parse(source, data, kind=None):
    kind = kind or SOURCES[source][1]
    if kind in ("summary", "custom-statuspage"):
        doc = json.loads(data)
        rows = doc["incidents"]
        if kind == "custom-statuspage":
            if not isinstance(doc["page"]["id"], str) or not doc["page"]["id"] or not isinstance(rows, list):
                raise ValueError("Missing Statuspage metadata")
            phases = {"investigating", "identified", "monitoring", "resolved", "postmortem"}
            maintenance = {"scheduled", "in_progress", "verifying", "completed"}
            for row in rows:
                if (row["status"] not in phases | maintenance
                        or not isinstance(row["id"], str) or not isinstance(row["name"], str)):
                    raise ValueError("Unsupported Statuspage incident")
            rows = [row for row in rows if row["status"] in phases and row["impact"] != "maintenance"]
        return [incident(row["id"], row["name"],
                         "resolved" if row["status"] in ("resolved", "postmortem") else "active",
                         {"minor": 1, "major": 2, "critical": 3}.get(row["impact"], 0),
                         [c["name"] for c in row.get("components", [])], row.get("resolved_at"))
                for row in rows[:250]]
    if kind == "slack":
        return [incident(row["id"], row["title"],
                         "resolved" if row["status"] == "resolved" else "active",
                         2 if row["type"] == "outage" else 1, row.get("services", []))
                for row in json.loads(data)[:250] if row["type"] in ("incident", "outage")]
    if kind == "google":
        return [incident(row["id"], row["external_desc"].splitlines()[0]
                         if not row["external_desc"].startswith("**Summary:**\n")
                         else row["external_desc"].splitlines()[1],
                         "resolved" if row.get("end") else "active",
                         {"SERVICE_DISRUPTION": 1, "SERVICE_OUTAGE": 2}.get(row.get("severity"), 0),
                         [p["title"] for p in row.get("affected_products", [])], row.get("end"))
                for row in json.loads(data)[:250]]
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("Unexpected XML declaration")
    channel = ET.fromstring(data).find("channel")
    if channel is None or not channel.findtext("title"):
        raise ValueError("Missing RSS channel")
    rows = []
    for item in channel.findall("item")[:350]:
        title = item.findtext("title", "")
        desc = item.findtext("description", "").lower()
        categories = {c.text.lower() for c in item.findall("category") if c.text}
        key = item.findtext("guid") or item.findtext("link")
        if kind == "notices":
            state = "notice"
        elif source == "grok":
            if not title.startswith("[Grok Bot]"):
                continue
            title = title.removeprefix("[Grok Bot]").strip()
            state = "resolved" if "resolved" in categories else "active" if "active" in categories else "notice"
        elif "maintenance" in categories:
            continue
        else:
            match = re.search(r"status:\s*(investigating|identified|monitoring|resolved|postmortem)\b", desc)
            if not match and source == "router":
                match = re.search(r"<strong>\s*(investigating|identified|monitoring|resolved|postmortem)\s*</strong>", desc)
            phase = match[1] if match else ""
            state = "resolved" if phase in ("resolved", "postmortem") else "active" if phase else "notice"
        severity = 2 if "outage" in categories else 1 if "disruption" in categories else 0
        rows.append(incident(key, title, state, severity))
    return rows

CHECK_DEADLINE = 18
PROBES = {
    "connection-google": ("https://connectivitycheck.gstatic.com/generate_204", b""),
    "connection-cloudflare": ("https://www.cloudflare.com/cdn-cgi/trace", b"fl="),
}

def diagnostic(key, reason):
    print(f"Omarchy Canary: {key}: {reason}", file=sys.stderr, flush=True)

def read_source(source):
    return parse(source, fetch(SOURCES[source][2]))

def read_custom_feed(url):
    return parse(None, fetch_custom_feed(url), "custom-statuspage")

def source_catalog(state):
    return {**SOURCES, **{
        key: (entry["name"], "custom-statuspage", entry["feed"], entry["url"])
        for key, entry in state.get("custom", {}).items() if entry.get("feed")
    }}

def probe(target):
    url, marker = target
    body = fetch(url)
    return body.startswith(marker) if marker else body == b""

def completed_checks(tasks, timeout):
    """Yield finished network checks until the deadline.

    This CLI runs one batch per process. A stuck network thread has no access
    to the cache, locks or stderr, and cannot delay process exit.
    """
    results = Queue()

    def check(key, function, argument):
        try:
            result = function(argument)
        except urllib.error.HTTPError as error:
            results.put((key, None, f"HTTP {error.code}"))
        except (OSError, http.client.HTTPException, ValueError, KeyError, TypeError,
                AttributeError, IndexError, ET.ParseError) as error:
            results.put((key, None, type(error).__name__))
        else:
            results.put((key, result, None))

    deadline = time.monotonic() + timeout
    pending = set(tasks)
    for key, (function, argument) in tasks.items():
        Thread(target=check, args=(key, function, argument), daemon=True).start()

    while pending:
        try:
            key, result, error = results.get(timeout=max(0, deadline - time.monotonic()))
        except Empty:
            break
        pending.remove(key)
        if error:
            diagnostic(key, error)
        yield key, result

def connection():
    tasks = {key: (probe, target) for key, target in PROBES.items()}
    return any(result is True for _, result in completed_checks(tasks, CHECK_DEADLINE))

def merge(previous, incoming, now):
    if incoming is None:
        return dict(previous, failures=previous.get("failures", 0) + 1), []
    known = {
        row["id"]: dict(row, confirmed=row.get("confirmed", previous.get("checked", 0)))
        for row in previous.get("events", [])
    }
    changes = []
    for row in incoming:
        old = known.get(row["id"])
        state = row["state"]
        if previous.get("checked") and not previous.get("baselinePending"):
            if state in ("active", "notice") and old is None:
                changes.append((state, row))
            elif state == "active" and old and old["state"] != "active":
                changes.append(("active", row))
            elif state == "active" and old and (row["severity"] > old["severity"]
                                                or set(row["scope"]) - set(old["scope"])):
                changes.append(("escalated", row))
            elif state == "resolved" and old and old["state"] == "active":
                changes.append(("resolved", row))
        event = dict(row, confirmed=now)
        if state == "resolved" and old:
            if old["state"] == "active":
                event["recoverySeenAt"] = now
            elif old["state"] == "resolved":
                for field in ("resolvedAt", "recoverySeenAt"):
                    if field in old:
                        event.setdefault(field, old[field])
        known[row["id"]] = event
    events = list(known.values())
    active = [row for row in events if row["state"] == "active"]
    history = [row for row in events if row["state"] != "active"]
    return {"events": active[:350] + history[-600:], "checked": now, "failures": 0}, changes

def refresh(state, force, commit=None, selected_only=False):
    now = time.time()
    last_attempt = state.get("attempt", 0)
    if not selected_only and not force:
        last_attempt = state.get("fullAttempt", last_attempt)
    if now - last_attempt < (5 if force else 270):
        return []
    sources = state.get("sources", {})
    catalog = source_catalog(state)
    selected = favourite_ids(state) | set(state.get("alerts", []))
    country = state.get("country", "US")
    due = [key for key in catalog if (key in selected or (key in SOURCES and not selected_only
                                                        and SOURCE_COUNTRIES.get(key, country) == country))
           and (force or now >= sources.get(key, {}).get("retry", 0))]
    tasks = {key: (read_source, key) if key in SOURCES else (read_custom_feed, catalog[key][2])
             for key in due}
    pending_probes = set(PROBES) if state.get("probe") else set()
    tasks.update({key: (probe, PROBES[key]) for key in pending_probes})
    pending_feeds = set(due)
    completed = {}
    reachable = False
    for key, result in completed_checks(tasks, CHECK_DEADLINE):
        if key in PROBES:
            pending_probes.discard(key)
            reachable = reachable or result is True
        else:
            pending_feeds.remove(key)
            completed[key] = result
            reachable = reachable or result is not None
        if not pending_feeds and (reachable or not pending_probes):
            break
    def apply(current):
        alerts = []
        subscriptions = set(current.get("alerts", [])) & set(state.get("alerts", []))
        current.setdefault("fullAttempt", current.get("attempt", 0))
        current["attempt"] = now
        if not selected_only:
            current["fullAttempt"] = now
        current_catalog = source_catalog(current)
        current_sources = current.setdefault("sources", {})
        for key in due:
            if key not in current_catalog or current_catalog[key][2] != catalog[key][2]:
                continue
            if key in pending_feeds:
                diagnostic(key, "refresh deadline exceeded")
            result = completed.get(key)
            entry, changes = merge(current_sources.get(key, {}), result, now)
            if result is None:
                entry["retry"] = now + min(1800, 300 * 2 ** min(entry["failures"] - 1, 3))
            current_sources[key] = entry
            if key in subscriptions and changes:
                alerts.append((current_catalog[key][0], current_catalog[key][3], changes))
        if current.get("probe") and state.get("probe"):
            current["connection"] = "reachable" if reachable else "unverified"
            if pending_probes and not reachable:
                diagnostic("connection", "refresh deadline exceeded")
        return alerts

    return commit(apply) if commit else apply(state)

def favourite_ids(state):
    country = state.get("country", "US")
    defaults = [key for key in DEFAULT_FAVOURITES if key not in LINKS or LINKS[key][0] in ("global", country)]
    return set(state.get("favourites", defaults))

def add_custom(state, key, name, url, endpoint, events):
    favourites = favourite_ids(state)
    if key is None and events is None:
        key = next((key for key, row in LINKS.items() if url == service_url(row[3])), None)
    if key is None:
        custom = state.setdefault("custom", {})
        key = next((key for key, entry in custom.items()
                    if entry["url"] == url or (endpoint and entry.get("feed") == endpoint)), None)
        key = key or "custom-" + uuid4().hex
        entry = custom.setdefault(key, {})
        entry.update(name=name, url=url)
        if events is not None and not entry.get("feed"):
            entry["feed"] = endpoint
            cache = state.setdefault("sources", {})
            cache[key], _ = merge(cache.get(key, {}), events, time.time())
    favourites.add(key)
    state["favourites"] = sorted(favourites)
    return key

def view(state):
    now = time.time()
    rows = []
    recoveries = []
    country = state.get("country", "US")
    favourites = favourite_ids(state)
    subscriptions = set(state.get("alerts", []))
    custom = state.get("custom", {})
    for key, (name, kind, _, url) in source_catalog(state).items():
        data = state.get("sources", {}).get(key, {})
        events = data.get("events", [])
        if key in favourites or key in subscriptions:
            for event in events:
                when = event.get("resolvedAt") or event.get("recoverySeenAt")
                if event["state"] == "resolved" and when and 0 <= now - when < 86400:
                    recoveries.append(dict(name=name, title=event["title"], url=url, at=when,
                                           reported=bool(event.get("resolvedAt"))))
        active = [e for e in events if e["state"] == "active"]
        checked = data.get("checked", 0)
        latest_notices = [e for e in events if e["state"] == "notice"]
        if data.get("failures", 0) >= 3 or (checked and now - checked > 900):
            status = "Status unknown"
        elif not checked:
            status = "Check failed" if data.get("failures") else "Awaiting first check"
        elif data.get("failures"):
            status = "Last check failed"
        elif active:
            status = f"{len(active)} active incident" + ("s" if len(active) != 1 else "")
        elif kind == "notices":
            status = "Official notice feed"
        elif latest_notices:
            status = "No confirmed active incident"
        else:
            status = "No active incident reported"
        fresh_active = [e for e in active if e.get("confirmed", checked) == checked]
        stale_active = [e for e in active if e not in fresh_active]
        if stale_active and not fresh_active and not data.get("failures") and now - checked <= 900:
            status = "Last known incident · awaiting confirmation"
        elif fresh_active and stale_active and not data.get("failures") and now - checked <= 900:
            status = f"{len(fresh_active)} currently reported · {len(stale_active)} awaiting confirmation"
        details = fresh_active + stale_active or latest_notices[:1]
        rows.append(dict(id=key, name=name, url=url, status=status, checked=checked,
                         active=bool(fresh_active), cachedActive=bool(active), missingActive=bool(stale_active), alert=key in state.get("alerts", []),
                         healthKnown=bool(checked and not data.get("failures") and (not stale_active or fresh_active)
                                          and kind != "notices"),
                         kind="feed", sourceLabel="Canary alerts", aliases=SEARCH_ALIASES.get(key, ()),
                         category="Custom services" if key in custom else CATEGORY_BY_ID.get(key, "Apps & cloud"),
                         custom=key in custom, favourite=key in favourites,
                         inDirectory=SOURCE_COUNTRIES.get(key, country) == country,
                         details=[dict(title=e["title"] + (" · " + ", ".join(e["scope"]) if e["scope"] else ""),
                                       confirmed=e.get("confirmed", checked), active=e["state"] == "active",
                                       current=e in fresh_active)
                                  for e in details[:4]]))
    for key, (region, name, label, url, *alert_help) in LINKS.items():
        rows.append(dict(id=key, name=name, url=url, kind="link", status=label,
                         sourceLabel=LINK_LABELS.get(key, ("Provider support" if label.endswith("support")
                                                          else "Provider outage checker")),
                         category=CATEGORY_BY_ID.get(key, "Apps & cloud" if region == "global" else "Internet & mobile"),
                         aliases=SEARCH_ALIASES.get(key, ()),
                         favourite=key in favourites, details=[], inDirectory=region in ("global", country),
                         alertHelp=alert_help[0] if alert_help else ""))
    for key, entry in custom.items():
        if not entry.get("feed"):
            rows.append(dict(id=key, name=entry["name"], url=entry["url"], kind="link", custom=True,
                             sourceLabel="Provider outage checker", category="Custom services",
                             status="Outage checker", favourite=key in favourites, details=[], inDirectory=True))
    rows.sort(key=lambda row: (not row["favourite"], row["name"].casefold()))
    present_categories = {row["category"] for row in rows if row["inDirectory"]}
    return dict(rows=rows, revision=state.get("revision", 0),
                categories=[name for name in CATEGORIES if name in present_categories],
                recoveries=sorted(recoveries, key=lambda event: event["at"], reverse=True)[:12],
                background=state.get("background", False), country=country, countries=COUNTRIES,
                providerCountries=sorted(({row[0] for row in LINKS.values()} | set(SOURCE_COUNTRIES.values())) - {"global"}),
                showAll=state.get("showAll", False),
                probe=state.get("probe", False), connection=state.get("connection", "unchecked"),
                quiet=state.get("quiet", False), snooze=state.get("snooze", 0) > now,
                attempt=state.get("attempt", 0))

def save(state):
    state["revision"] = state.get("revision", 0) + 1
    file = tempfile.NamedTemporaryFile(mode="w", dir=STATE, delete=False)
    try:
        with file:
            json.dump(state, file)
        os.replace(file.name, STATE / "state.json")
    finally:
        Path(file.name).unlink(missing_ok=True)

def send_notification(body, actions=()):
    icon = Path(__file__).resolve().with_name("notification-icon.png")
    command = ["notify-send", "-a", "Omarchy Canary", "-h", f"string:image-path:{icon}"]
    if actions:
        command.extend(("-h", "string:omarchy-actions:" + json.dumps(actions)))
    result = subprocess.run(command + ["--", "Omarchy Canary", body], timeout=4, check=False)
    return result.returncode == 0

def notify_incidents(alerts):
    labels = {"active": "new incident", "notice": "new notice", "escalated": "impact increased", "resolved": "resolved"}
    lines = []
    actions = []
    for name, url, changes in alerts:
        for change, event in changes:
            scope = ", ".join(event["scope"][:2])
            line = " · ".join((name, labels[change], clean(event["title"], 140)))
            if scope:
                line += " · " + clean(scope, 90)
            lines.append(line)
        try:
            status_page = service_url(url)
        except ValueError:
            continue
        label = "Open status page" if len(alerts) == 1 else "Open " + name
        actions.append({"label": label, "argv": ["xdg-open", status_page]})
    if len(actions) > 3:
        actions = actions[:2] + [{"label": "Open Canary",
                                 "argv": ["omarchy-shell", "shaggyrs6.service-outages", "show"]}]
    visible = lines[:4]
    if len(lines) > len(visible):
        visible.append(f"+{len(lines) - len(visible)} more updates")
    return send_notification("\n".join(visible), actions)

def read_state():
    try:
        return json.loads((STATE / "state.json").read_text())
    except FileNotFoundError:
        return {}

@contextlib.contextmanager
def locked_state():
    with (STATE / "lock").open("a") as lock:
        deadline = time.monotonic() + 2
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("another command is still running") from None
                time.sleep(0.05)
        yield read_state()

def commit_refresh(apply):
    with locked_state() as state:
        alerts = apply(state)
        save(state)
        return alerts

def run_refresh(command):
    with (STATE / "refresh.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return read_state()
        state = read_state()
        if command == "poll" and not state.get("background"):
            return state
        alerts = refresh(state, command == "refresh", commit_refresh,
                         selected_only=command == "poll" and not state.get("showAll"))

    state = read_state()
    muted = state.get("snooze", 0) > time.time() or (
        state.get("quiet") and (dt.datetime.now().hour < 8 or dt.datetime.now().hour >= 22))
    if alerts and not muted:
        try:
            notify_incidents(alerts)
        except (OSError, subprocess.TimeoutExpired):
            pass
    return read_state()

def run_command(args):
    command = args[0]
    if command == "test-alert":
        try:
            sent = send_notification(
                "Test alert · GitHub · example incident\nAPI requests failing",
                [{"label": "Open status page", "argv": ["xdg-open", SOURCES["github"][3]]}],
            )
        except (OSError, subprocess.TimeoutExpired):
            sent = False
        print(json.dumps({"testAlertSent": sent}))
        return
    if command == "add-custom":
        try:
            name = clean(args[1], 80)
            if not name:
                raise ValueError("Enter a service name.")
            url = service_url(args[2])
        except ValueError as error:
            print(json.dumps({"inputError": str(error)}))
            return
        endpoint = statuspage_endpoint(url)
        builtin = next((key for key, (_, _, feed, page) in SOURCES.items()
                        if url in (service_url(feed), service_url(page))
                        or (endpoint and endpoint == statuspage_endpoint(page))), None)
        events = None
        if endpoint and builtin is None:
            events = next((result for _, result in completed_checks(
                {"custom feed": (read_custom_feed, endpoint)}, 7)), None)

    STATE.mkdir(parents=True, mode=0o700, exist_ok=True)
    if command in ("refresh", "visit", "poll"):
        print(json.dumps(view(run_refresh(command))))
        return
    if command == "status":
        print(json.dumps(view(read_state())))
        return

    added = None
    with locked_state() as state:
        if command in ("background", "probe", "quiet", "showAll"):
            state[command] = args[1] == "true"
            if command == "probe" and not state[command]:
                state["connection"] = "unchecked"
        elif command == "add-custom":
            added = add_custom(state, builtin, name, url, endpoint, events)
        elif command == "remove-custom":
            key = args[1]
            if key in state.get("custom", {}):
                state["favourites"] = sorted(favourite_ids(state) - {key})
                state["alerts"] = [choice for choice in state.get("alerts", []) if choice != key]
                state.get("sources", {}).pop(key, None)
                del state["custom"][key]
        elif command == "alert" and args[1] in source_catalog(state):
            choices = set(state.get("alerts", []))
            if args[1] in choices:
                choices.remove(args[1])
            else:
                choices.add(args[1])
                entry = state.get("sources", {}).get(args[1])
                if entry and time.time() - entry.get("checked", 0) > 900:
                    entry["baselinePending"] = True
            state["alerts"] = sorted(choices)
        elif command == "country" and args[1] in COUNTRIES:
            state["country"] = args[1]
        elif command == "favourite" and (args[1] in SOURCES or args[1] in LINKS or args[1] in state.get("custom", {})):
            choices = favourite_ids(state)
            choices.symmetric_difference_update([args[1]])
            state["favourites"] = sorted(choices)
        elif command == "snooze":
            state["snooze"] = time.time() + 3600 if args[1] == "true" else 0
        else:
            raise ValueError("Unknown command")
        save(state)

    if command == "background" and state["background"]:
        state = run_refresh("poll")
    elif command == "probe" and state["probe"]:
        connection_status = "reachable" if connection() else "unverified"
        attempt = state.get("attempt")
        with locked_state() as state:
            if state.get("probe") and state.get("attempt") == attempt:
                state["connection"] = connection_status
                save(state)
    result = view(state)
    if added:
        result["added"] = added
    print(json.dumps(result))

def main(args):
    try:
        return run_command(args)
    except TimeoutError:
        diagnostic("state", "another command is still running")
        return 1

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
