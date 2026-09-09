"""
Category Mapper  (v3 --- Intelligent Matching Engine)
====================================================

Replaces the naive SequenceMatcher approach with a multi-signal scorer:

Signal 1 --- Token Set Overlap
    Tokenise both sides, expand synonyms, then compute a Dice-coefficient
    style overlap.  Order-independence means "Ceramic Capacitor 10uF" and
    "Capacitor, Ceramic" score identically.

Signal 2 --- Hierarchy Leaf Bias
    The right-most segment of a ">" path is the most specific.  Its tokens
    are given 3-- weight.  The second-to-last segment gets 1.5-- weight.

Signal 3 --- Synonym Expansion
    A user-configurable synonym table (stored as JSON in plugin settings)
    is applied before tokenisation.  Matching after expansion is treated
    as an exact token hit rather than a fuzzy one.

Signal 4 --- Prefix Bonus
    If the distributor leaf node starts with (or is a prefix of) the
    InvenTree leaf node, add a small bonus.

Final score formula (0-100):
    score = 70 * token_dice + 20 * leaf_dice + 10 * prefix_bonus

Threshold default: 45  (lower than before because scoring is tighter)
"""

import json
import logging
import re
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger("inventree_smart_parts.services.category")

# -----------------------------------------------------------------------------
#  Category cache
# -----------------------------------------------------------------------------
_category_cache_flat: Optional[List[Dict]] = None

# -----------------------------------------------------------------------------
#  Built-in synonym table  (always active, augmented by user synonyms)
# -----------------------------------------------------------------------------
_BUILTIN_SYNONYMS: Dict[str, str] = {
    # Capacitors
    "mlcc": "ceramic capacitor",
    "electrolytic": "electrolytic capacitor",
    "tantalum": "tantalum capacitor",
    "film cap": "film capacitor",
    "cap": "capacitor",
    "caps": "capacitors",
    # Resistors
    "res": "resistor",
    "resistors": "resistor",
    "trimmer": "trimmer resistor",
    "potentiometer": "resistor",
    "varistor": "resistor",
    "thermistor": "resistor",
    "ntc": "thermistor",
    "ptc": "thermistor",
    # Inductors / Magnetics
    "inductor": "inductor",
    "inductors": "inductor",
    "ferrite bead": "ferrite",
    "choke": "inductor",
    "transformer": "transformer",
    # Semiconductors
    "ic": "integrated circuit",
    "ics": "integrated circuit",
    "mcu": "microcontroller",
    "microcontrollers": "microcontroller",
    "mpu": "microprocessor",
    "fpga": "programmable logic",
    "cpld": "programmable logic",
    "dsp": "digital signal processor",
    "opamp": "op amp",
    "op-amp": "op amp",
    "operational amplifier": "op amp",
    "comparator": "comparator",
    "voltage reference": "voltage reference",
    "vreg": "voltage regulator",
    "ldo": "voltage regulator",
    "buck": "dc dc converter",
    "boost": "dc dc converter",
    "dcdc": "dc dc converter",
    "dc dc": "dc dc converter",
    "switching regulator": "dc dc converter",
    "power management": "power",
    "pmic": "power management ic",
    # MOSFETs / Transistors
    "mosfet": "mosfet",
    "mosfets": "mosfet",
    "bjt": "transistor",
    "jfet": "transistor",
    "igbt": "transistor",
    # Diodes
    "diode": "diode",
    "led": "led",
    "light emitting diode": "led",
    "zener": "zener diode",
    "schottky": "schottky diode",
    "tvs": "transient voltage suppressor",
    "esd": "protection",
    # Connectors
    "connector": "connector",
    "connectors": "connector",
    "header": "connector",
    "socket": "connector",
    "terminal": "connector",
    "terminals": "connector",
    # Switches / Relays
    "switch": "switch",
    "relay": "relay",
    "pushbutton": "switch",
    # Passives generic
    "passive": "passives",
    "passives": "passives",
    "discrete": "discrete",
    # Crystals / Oscillators
    "crystal": "crystal",
    "xtal": "crystal",
    "oscillator": "oscillator",
    "resonator": "resonator",
    # Sensors
    "sensor": "sensor",
    "sensors": "sensor",
    "accelerometer": "sensor",
    "gyroscope": "sensor",
    "temperature sensor": "sensor",
    "humidity sensor": "sensor",
    "pressure sensor": "sensor",
    # RF / Wireless
    "rf": "rf",
    "antenna": "antenna",
    "bluetooth": "wireless",
    "wifi": "wireless",
    "wi fi": "wireless",
    "zigbee": "wireless",
    "module": "module",
    # Mechanical
    "fuse": "fuse",
    "heatsink": "thermal",
    "heat sink": "thermal",
    "fan": "cooling",
    # Memory
    "flash": "memory",
    "eeprom": "memory",
    "sram": "memory",
    "dram": "memory",
    "ram": "memory",
    "rom": "memory",
    # Interface
    "uart": "interface",
    "spi": "interface",
    "i2c": "interface",
    "usb": "interface",
    "can": "interface",
    "ethernet": "interface",
    # Displays
    "lcd": "display",
    "oled": "display",
    "display": "display",
    # German Category Taxonomy Synonyms
    "operationsverstärker": "amplifiers opamps",
    "operationsverstaerker": "amplifiers opamps",
    "präzisionsverstärker": "amplifiers opamps",
    "praezisionsverstaerker": "amplifiers opamps",
    "trennverstärker": "amplifiers opamps",
    "trennverstaerker": "amplifiers opamps",
    "analoge komparatoren": "amplifiers opamps",
    "leistungsinduktivitäten": "power inductors",
    "leistungsinduktivitaeten": "power inductors",
    "spule": "power inductors",
    "spulen": "power inductors",
    "gleichtaktdrosseln": "ferrite beads",
    "audio transformatoren": "inductors transformers",
    "signal transformatoren": "inductors transformers",
    "kondensator aus mehreren keramikschichten": "ceramic capacitors",
    "keramikschichten": "ceramic capacitors",
    "kondensator": "capacitors",
    "kondensatoren": "capacitors",
    "keramikkondensator": "ceramic capacitors",
    "keramikkondensatoren": "ceramic capacitors",
    "aluminium elektrolyt kondensatoren": "aluminium electrolytic capacitors",
    "aluminium elektrolyt": "aluminium electrolytic",
    "elektrolyt kondensatoren": "electrolytic capacitors",
    "superkondensatoren": "electrolytic capacitors",
    "ultra kondensatoren": "electrolytic capacitors",
    "dc/dc wandler": "dc dc converters",
    "dc dc wandler": "dc dc converters",
    "schaltspannungsregler": "dc dc converters",
    "schaltregler": "dc dc converters",
    "ac/dc wandler": "dc dc converters",
    "ac dc wandler": "dc dc converters",
    "lineare spannungsregler": "ldo regulators",
    "ldo spannungs regulator": "ldo regulators",
    "hot swap spannungscontroller": "power management",
    "überwachungsschaltungen": "power management",
    "ueberwachungsschaltungen": "power management",
    "batterie management": "power management",
    "led stromversorgung": "power management",
    "energieverwaltung": "power management",
    "mosfets": "mosfet transistors",
    "mosfet module": "mosfet transistors",
    "sicherungen zur aufbaumontage": "fuses circuit protection",
    "sicherungen": "fuses",
    "multiplex schalter ic": "logic integrated circuits",
    "galvanisch isolierte gate treiber": "logic integrated circuits",
    "gate treiber": "logic integrated circuits",
    "digitale isolatoren": "logic integrated circuits",
    "isolatoren": "logic integrated circuits",
    "flip flops": "logic integrated circuits",
    "taktgeneratoren": "logic integrated circuits",
    "zeitgeber": "logic integrated circuits",
    "8 bit mikrocontroller": "microcontroller integrated circuits",
    "mikrocontroller": "microcontroller",
    "wippschalter": "switches electromechanical",
    "mech schalter": "switches electromechanical",
    "reed relais": "relays electromechanical",
    "relais": "relays",
    "usb stecker": "headers wire housings",
    "stecker": "connectors",
    "melf widerstände": "surface mount smd resistors",
    "melf widerstaende": "surface mount smd resistors",
    "dünnfilmwiderstände": "surface mount smd resistors",
    "duennfilmwiderstaende": "surface mount smd resistors",
    "stromsensoren": "sensors transducers",
    # ════════════════════════════════════════════════════════════════
    # Widerstand & Widerstände
    # ════════════════════════════════════════════════════════════════
    "widerstand": "Resistance",
    "widerstandswert": "Resistance",
    "widerstandsmesswert": "Resistance",
    "widerstand (ohm)": "Resistance",
    "widerstand ohm": "Resistance",
    "nennwiderstand": "Resistance",
    "nennwert widerstand": "Resistance",
    "sollwiderstand": "Resistance",
    "widerstandsbereich": "Resistance",
    "widerstandswertebereich": "Resistance",
    "r-wert": "Resistance",
    "widerstandstoleranz": "Resistance Tolerance",
    "toleranz widerstand": "Resistance Tolerance",
    "toleranz (widerstand)": "Resistance Tolerance",
    "widerstands-toleranz": "Resistance Tolerance",
    "genauigkeit widerstand": "Resistance Tolerance",
    "temperaturkoeffizient": "Temperature Coefficient (TCR)",
    "temperaturkoeffizient des widerstands": "Temperature Coefficient (TCR)",
    "temperaturkoeffizient (ppm/k)": "Temperature Coefficient (TCR)",
    "temperaturkoeffizient (ppm/°c)": "Temperature Coefficient (TCR)",
    "tk": "Temperature Coefficient (TCR)",
    "tkr": "Temperature Coefficient (TCR)",
    "tc": "Temperature Coefficient (TCR)",
    # NEU: Nennleistung / Belastbarkeit taucht bei praktisch jedem
    # Widerstand/Bauteil bei RS, Mouser, Digi-Key DE als eigenes Feld auf
    "nennleistung": "Power Rating",
    "belastbarkeit": "Power Rating",
    "leistung": "Power Rating",
    "verlustleistung": "Power Rating",
    "max. leistung": "Power Rating",
    "leistungsaufnahme": "Power Rating",
    "leistungsangabe": "Power Rating",
    "watt": "Power Rating",
    # ════════════════════════════════════════════════════════════════
    # Kapazität & Kondensatoren
    # ════════════════════════════════════════════════════════════════
    "kapazität": "Capacitance",
    "kapazitaet": "Capacitance",
    "kapazitätswert": "Capacitance",
    "nennkapazität": "Capacitance",
    "nennkapazitaet": "Capacitance",
    "sollkapazität": "Capacitance",
    "kondensatorwert": "Capacitance",
    "kapazitätsbereich": "Capacitance",
    "c-wert": "Capacitance",
    "kapazitätstoleranz": "Capacitance Tolerance",
    "kapazitaetstoleranz": "Capacitance Tolerance",
    "toleranz kondensator": "Capacitance Tolerance",
    "toleranz (kondensator)": "Capacitance Tolerance",
    "genauigkeit kondensator": "Capacitance Tolerance",
    "dielektrikum": "Dielectric Material",
    "dielektrisches material": "Dielectric Material",
    "dielektrikumstyp": "Dielectric Material",
    "dielektrizitätsklasse": "Dielectric Material",
    "keramikklasse": "Dielectric Material",
    "kondensatortyp": "Dielectric Material",
    "kondensator typ": "Dielectric Material",
    "äquivalenter serienwiderstand": "Equivalent Series Resistance (ESR)",
    "aequivalenter serienwiderstand": "Equivalent Series Resistance (ESR)",
    "esr": "Equivalent Series Resistance (ESR)",
    "esr (ohm)": "Equivalent Series Resistance (ESR)",
    "max. esr": "Equivalent Series Resistance (ESR)",
    "serienwiderstand": "Equivalent Series Resistance (ESR)",
    "rippelstrom": "Ripple Current",
    "welligkeitsstrom": "Ripple Current",
    "max. rippelstrom": "Ripple Current",
    "zulässiger rippelstrom": "Ripple Current",
    "zulaessiger rippelstrom": "Ripple Current",
    "brummstrom": "Ripple Current",
    "leckstrom": "Leakage Current",
    "kriechstrom": "Leakage Current",
    "max. leckstrom": "Leakage Current",
    "dc-leckstrom": "Leakage Current",
    "ableitstrom": "Leakage Current",
    # ════════════════════════════════════════════════════════════════
    # Induktivität & Magnetika
    # ════════════════════════════════════════════════════════════════
    "induktivität": "Inductance",
    "induktivitaet": "Inductance",
    "induktivitätswert": "Inductance",
    "nenninduktivität": "Inductance",
    "nenninduktivitaet": "Inductance",
    "l-wert": "Inductance",
    "induktivitätstoleranz": "Inductance Tolerance",
    "induktivitaetstoleranz": "Inductance Tolerance",
    "toleranz spule": "Inductance Tolerance",
    "toleranz induktivität": "Inductance Tolerance",
    "gütefaktor": "Q Factor",
    "guetefaktor": "Q Factor",
    "q-faktor": "Q Factor",
    "güte": "Q Factor",
    "guete": "Q Factor",
    "min. güte": "Q Factor",
    "min. guete": "Q Factor",
    "eigenresonanzfrequenz": "Self Resonant Frequency (SRF)",
    "resonanzfrequenz": "Self Resonant Frequency (SRF)",
    "eigenresonanz": "Self Resonant Frequency (SRF)",
    "srf": "Self Resonant Frequency (SRF)",
    "min. srf": "Self Resonant Frequency (SRF)",
    "gleichstromwiderstand": "DC Resistance (DCR)",
    "dc-widerstand": "DC Resistance (DCR)",
    "dcr": "DC Resistance (DCR)",
    "max. dcr": "DC Resistance (DCR)",
    "spulenwiderstand (dc)": "DC Resistance (DCR)",
    "kernmaterial": "Core Material",
    "spulenkern": "Core Material",
    "kerntyp": "Core Material",
    "sättigungsstrom": "Saturation Current (Isat)",
    "saettigungsstrom": "Saturation Current (Isat)",
    "isat": "Saturation Current (Isat)",
    "max. sättigungsstrom": "Saturation Current (Isat)",
    "temperaturanstiegsstrom": "Temperature Rise Current (Itemp)",
    "effektivstrom": "Temperature Rise Current (Itemp)",
    "itemp": "Temperature Rise Current (Itemp)",
    "nennstrom (temperaturanstieg)": "Temperature Rise Current (Itemp)",
    # ════════════════════════════════════════════════════════════════
    # Spannungsangaben
    # ════════════════════════════════════════════════════════════════
    "spannung": "Voltage Rating",
    "nennspannung": "Voltage Rating",
    "betriebsspannung": "Voltage Rating",
    "max. spannung": "Voltage Rating",
    "maximale spannung": "Voltage Rating",
    "spannungsfestigkeit": "Voltage Rating",
    "ausgangsspannung": "Voltage Rating",
    "spannung dc": "Voltage Rating",
    "spannung ac": "Voltage Rating",
    "spannung (dc)": "Voltage Rating",
    "spannung (ac)": "Voltage Rating",
    "arbeitsspannung": "Voltage Rating",
    "vdc": "Voltage Rating",
    "minimale betriebsspannung": "Voltage Rating",
    "maximale betriebsspannung": "Voltage Rating",
    # ════════════════════════════════════════════════════════════════
    # Montageart / Bauform (Bestückung)
    # ════════════════════════════════════════════════════════════════
    "montageart": "Mounting Type",
    "bauform montage": "Mounting Type",
    "befestigungsart": "Mounting Type",
    "montage": "Mounting Type",
    "montagetyp": "Mounting Type",
    "bestückungsart": "Mounting Type",
    "bestueckungsart": "Mounting Type",
    "smd/tht": "Mounting Type",
    "smd / tht": "Mounting Type",
    # NEU: Anschlussart / Axial-Radial-SMD -> eigene Kategorie,
    # da bei RS/Farnell meist getrennt von "Termination Style" geführt
    "anschlussart": "Terminal Style",
    "axial/radial": "Terminal Style",
    "axial / radial": "Terminal Style",
    "anschlusstyp (form)": "Terminal Style",
    "bauform anschluss": "Terminal Style",
    "gehäuseform anschluss": "Terminal Style",
    "leitungsform": "Terminal Style",
    # ════════════════════════════════════════════════════════════════
    # Grundlegende Halbleiterwerte
    # ════════════════════════════════════════════════════════════════
    "durchlassspannung": "Forward Voltage",
    "vorwärtsspannung": "Forward Voltage",
    "vorwaertsspannung": "Forward Voltage",
    "max. durchlassspannung": "Forward Voltage",
    "flussspannung": "Forward Voltage",
    "vf": "Forward Voltage",
    "sperrspannung": "Reverse Voltage",
    "rückwärtsspannung": "Reverse Voltage",
    "rueckwaertsspannung": "Reverse Voltage",
    "max. sperrspannung": "Reverse Voltage",
    "dc-sperrspannung": "Reverse Voltage",
    "vr": "Reverse Voltage",
    "sperrstrom": "Reverse Current",
    "rückwärtsstrom": "Reverse Current",
    "rueckwaertsstrom": "Reverse Current",
    "max. sperrstrom": "Reverse Current",
    "sperrschichtleckstrom": "Reverse Current",
    "ir": "Reverse Current",
    "zener-spannung": "Zener Voltage",
    "zenerspannung": "Zener Voltage",
    "z-spannung": "Zener Voltage",
    "nennzenerspannung": "Zener Voltage",
    "vz": "Zener Voltage",
    "sperrverzugszeit": "Reverse Recovery Time",
    "rückwärtserholzeit": "Reverse Recovery Time",
    "rueckwaertserholzeit": "Reverse Recovery Time",
    "erholzeit": "Reverse Recovery Time",
    "trr": "Reverse Recovery Time",
    # ════════════════════════════════════════════════════════════════
    # Transistoren (BJT & MOSFET)
    # ════════════════════════════════════════════════════════════════
    "stromverstärkung": "Current Gain (hFE)",
    "stromverstaerkung": "Current Gain (hFE)",
    "gleichstromverstärkung": "Current Gain (hFE)",
    "gleichstromverstaerkung": "Current Gain (hFE)",
    "hfe": "Current Gain (hFE)",
    "hfe min.": "Current Gain (hFE)",
    "kollektor-emitter-sättigungsspannung": "Collector-Emitter Saturation Voltage",
    "kollektor-emitter-saettigungsspannung": "Collector-Emitter Saturation Voltage",
    "vce sat": "Collector-Emitter Saturation Voltage",
    "vce(sat)": "Collector-Emitter Saturation Voltage",
    "kollektorstrom": "Continuous Collector Current",
    "dauerkollektorstrom": "Continuous Collector Current",
    "max. kollektorstrom": "Continuous Collector Current",
    "ic": "Continuous Collector Current",
    "drainstrom": "Continuous Drain Current (Id)",
    "dauerdrainstrom": "Continuous Drain Current (Id)",
    "max. drainstrom": "Continuous Drain Current (Id)",
    "id": "Continuous Drain Current (Id)",
    "drain-source-spannung": "Drain to Source Voltage (Vdss)",
    "drain-source-durchbruchspannung": "Drain to Source Voltage (Vdss)",
    "vdss": "Drain to Source Voltage (Vdss)",
    "gate-source-schwellenspannung": "Gate to Source Threshold Voltage (Vgs th)",
    "gate-schwellenspannung": "Gate to Source Threshold Voltage (Vgs th)",
    "vgs(th)": "Gate to Source Threshold Voltage (Vgs th)",
    "einschaltwiderstand": "On Resistance (Rds On)",
    "durchlasswiderstand": "On Resistance (Rds On)",
    "rds(on)": "On Resistance (Rds On)",
    "rds on": "On Resistance (Rds On)",
    "gate-ladung": "Gate Charge",
    "gesamtgateladung": "Gate Charge",
    "qg": "Gate Charge",
    "eingangskapazität": "Input Capacitance",
    "eingangskapazitaet": "Input Capacitance",
    "ciss": "Input Capacitance",
    "ausgangskapazität": "Output Capacitance",
    "ausgangskapazitaet": "Output Capacitance",
    "coss": "Output Capacitance",
    "sperrverzugsladung": "Reverse Recovery Charge (Qrr)",
    "qrr": "Reverse Recovery Charge (Qrr)",
    # ════════════════════════════════════════════════════════════════
    # Integrierte Schaltungen (ICs) & Leistung
    # ════════════════════════════════════════════════════════════════
    "versorgungsspannung": "Supply Voltage",
    "betriebsspannungsbereich": "Supply Voltage",
    "versorgungsspannungsbereich": "Supply Voltage",
    "vcc": "Supply Voltage",
    "vdd": "Supply Voltage",
    "versorgungsstrom": "Supply Current",
    "betriebsstrom": "Supply Current",
    "ruhestrom": "Supply Current",
    "icc": "Supply Current",
    "idd": "Supply Current",
    "ausgangsstrom": "Output Current",
    "max. ausgangsstrom": "Output Current",
    "dauerausgangsstrom": "Output Current",
    "iout": "Output Current",
    "schnittstelle": "Interface",
    "kommunikationsschnittstelle": "Interface",
    "anschlussmöglichkeiten": "Interface",
    "anschlussmoeglichkeiten": "Interface",
    "bus-schnittstelle": "Interface",
    "protokoll": "Interface",
    "speichergröße": "Memory Size",
    "speichergroesse": "Memory Size",
    "speicherkapazität": "Memory Size",
    "speicherkapazitaet": "Memory Size",
    "programmspeichergröße": "Memory Size",
    "programmspeichergroesse": "Memory Size",
    "ram-größe": "Memory Size",
    "flash-größe": "Memory Size",
    "speichertyp": "Memory Type",
    "speicherart": "Memory Type",
    "nichtflüchtiger speichertyp": "Memory Type",
    "taktfrequenz": "Clock Frequency",
    "taktrate": "Clock Frequency",
    "oszillatorfrequenz": "Clock Frequency",
    "max. taktfrequenz": "Clock Frequency",
    "pinanzahl": "Pin Count",
    "anzahl pins": "Pin Count",
    "anzahl anschlüsse": "Pin Count",
    "anzahl anschluesse": "Pin Count",
    "anzahl der anschlüsse": "Pin Count",
    "anschlusszahl": "Pin Count",
    "polzahl": "Pin Count",
    "prozessorkern": "Core Processor",
    "kernarchitektur": "Core Processor",
    "cpu-kern": "Core Processor",
    "kernbreite": "Core Width",
    "datenbusbreite": "Core Width",
    "busbreite": "Core Width",
    "bit-breite": "Core Width",
    "adc-/dac-auflösung": "ADC / DAC Resolution",
    "adc-/dac-aufloesung": "ADC / DAC Resolution",
    "wandlerauflösung": "ADC / DAC Resolution",
    "wandleraufloesung": "ADC / DAC Resolution",
    "auflösung": "ADC / DAC Resolution",
    "aufloesung": "ADC / DAC Resolution",
    "gleichtaktunterdrückung": "Common Mode Rejection Ratio (CMRR)",
    "gleichtaktunterdrueckung": "Common Mode Rejection Ratio (CMRR)",
    "cmrr": "Common Mode Rejection Ratio (CMRR)",
    "anstiegsrate": "Slew Rate",
    "flankensteilheit": "Slew Rate",
    "slew rate": "Slew Rate",
    "logiktyp": "Logic Type",
    "logikfamilie": "Logic Type",
    "logikfunktion": "Logic Type",
    "ausgangstyp": "Output Type",
    "ausgangskonfiguration": "Output Type",
    "logikausgangstyp": "Output Type",
    "referenzspannung": "Reference Voltage",
    "spannungsreferenz": "Reference Voltage",
    "interne referenzspannung": "Reference Voltage",
    "eingangsvorstrom": "Input Bias Current",
    "eingangsruhestrom": "Input Bias Current",
    "eingangsoffsetspannung": "Input Offset Voltage",
    "offsetspannung": "Input Offset Voltage",
    "anzahl kanäle": "Number of Channels",
    "anzahl kanaele": "Number of Channels",
    "kanalanzahl": "Number of Channels",
    "kanäle": "Number of Channels",
    "kanaele": "Number of Channels",
    "anzahl ausgänge": "Number of Outputs",
    "anzahl ausgaenge": "Number of Outputs",
    "ausgänge": "Number of Outputs",
    "ausgaenge": "Number of Outputs",
    # ════════════════════════════════════════════════════════════════
    # Elektromechanik & Mechanik
    # ════════════════════════════════════════════════════════════════
    "schaltkonfiguration": "Circuit / Contact Form",
    "kontaktform": "Circuit / Contact Form",
    "schaltkontakt": "Circuit / Contact Form",
    "schaltbild": "Circuit / Contact Form",
    "polzahl und schaltstellungen": "Circuit / Contact Form",
    "kontaktbelastbarkeit": "Contact Rating",
    "kontaktstrom": "Contact Rating",
    "schaltleistung": "Contact Rating",
    "max. kontaktstrom": "Contact Rating",
    "kontaktwiderstand": "Contact Resistance",
    "max. kontaktwiderstand": "Contact Resistance",
    "übergangswiderstand": "Contact Resistance",
    "uebergangswiderstand": "Contact Resistance",
    "isolationswiderstand": "Insulation Resistance",
    "min. isolationswiderstand": "Insulation Resistance",
    "isolierwiderstand": "Insulation Resistance",
    "durchschlagfestigkeit": "Dielectric Strength",
    "spannungsfestigkeit (isolation)": "Dielectric Strength",
    "prüfspannung": "Dielectric Strength",
    "pruefspannung": "Dielectric Strength",
    "betätigungsart": "Actuator Type",
    "betaetigungsart": "Actuator Type",
    "schalterbetätigung": "Actuator Type",
    "schalterbetaetigung": "Actuator Type",
    "betätigungselement": "Actuator Type",
    "beleuchtung": "Illumination",
    "hintergrundbeleuchtung": "Illumination",
    "beleuchtungsart": "Illumination",
    "beleuchtet": "Illumination",
    "spulenspannung": "Coil Voltage",
    "relaisspulenspannung": "Coil Voltage",
    "spulennennspannung": "Coil Voltage",
    "spulenwiderstand": "Coil Resistance",
    "relaisspulenwiderstand": "Coil Resistance",
    "spulenleistung": "Coil Power",
    "spulenleistungsaufnahme": "Coil Power",
    "kontaktmaterial": "Contact Material",
    "relaiskontaktmaterial": "Contact Material",
    "kontaktbeschichtung": "Contact Material",
    "reihenanzahl": "Row Count",
    "anzahl reihen": "Row Count",
    "reihen": "Row Count",
    "raster": "Pitch",
    "rastermaß": "Pitch",
    "rastermass": "Pitch",
    "kontaktabstand": "Pitch",
    "pinabstand": "Pitch",
    "polabstand": "Pitch",
    "geschlecht": "Gender / Type",
    "steckertyp": "Gender / Type",
    "kontakttyp": "Gender / Type",
    "stecker/buchse": "Gender / Type",
    "stecker / buchse": "Gender / Type",
    "montageausrichtung": "Mounting Orientation",
    "einbaulage": "Mounting Orientation",
    "montagewinkel": "Mounting Orientation",
    "gerade/gewinkelt": "Mounting Orientation",
    "luftstrom": "Fan Airflow",
    "förderleistung": "Fan Airflow",
    "foerderleistung": "Fan Airflow",
    "luftdurchsatz": "Fan Airflow",
    "lüfterdrehzahl": "Fan Speed",
    "luefterdrehzahl": "Fan Speed",
    "drehzahl": "Fan Speed",
    "nenndrehzahl": "Fan Speed",
    "statischer druck": "Fan Static Pressure",
    "förderdruck": "Fan Static Pressure",
    "foerderdruck": "Fan Static Pressure",
    "lüftergeräusch": "Fan Noise",
    "lueftergeraeusch": "Fan Noise",
    "geräuschpegel": "Fan Noise",
    "geraeuschpegel": "Fan Noise",
    "schalldruckpegel": "Fan Noise",
    "lagertyp": "Fan Bearing Type",
    "lagerart": "Fan Bearing Type",
    "lager": "Fan Bearing Type",
    "lüfternennspannung": "Fan Rated Voltage",
    "luefternennspannung": "Fan Rated Voltage",
    "lüfter betriebsspannung": "Fan Rated Voltage",
    # ════════════════════════════════════════════════════════════════
    # Allgemein & Umgebungsbedingungen
    # ════════════════════════════════════════════════════════════════
    "betriebstemperatur": "Operating Temperature",
    "betriebstemperaturbereich": "Operating Temperature",
    "arbeitstemperaturbereich": "Operating Temperature",
    "betriebstemperatur min.": "Operating Temperature",
    "betriebstemperatur max.": "Operating Temperature",
    "minimale betriebstemperatur": "Operating Temperature",
    "maximale betriebstemperatur": "Operating Temperature",
    "einsatztemperaturbereich": "Operating Temperature",
    "lagertemperatur": "Storage Temperature",
    "lagertemperaturbereich": "Storage Temperature",
    "lagertemperatur min.": "Storage Temperature",
    "lagertemperatur max.": "Storage Temperature",
    "minimale lagertemperatur": "Storage Temperature",
    "maximale lagertemperatur": "Storage Temperature",
    "gehäuse": "Package / Case",
    "gehaeuse": "Package / Case",
    "gehäuseform": "Package / Case",
    "gehaeuseform": "Package / Case",
    "gehäusegröße": "Package / Case",
    "gehaeusegroesse": "Package / Case",
    "bauform": "Package / Case",
    "verpackung": "Package / Case",
    "bauteilgehäuse": "Package / Case",
    "anschlussart (termination)": "Termination Style",
    "terminierung": "Termination Style",
    "anschlussweise": "Termination Style",
    "anschlusstechnik": "Termination Style",
    "feuchteempfindlichkeitsstufe": "Moisture Sensitivity Level (MSL)",
    "feuchteempfindlichkeitsklasse": "Moisture Sensitivity Level (MSL)",
    "msl": "Moisture Sensitivity Level (MSL)",
    "msl-stufe": "Moisture Sensitivity Level (MSL)",
    "rohs-status": "RoHS Status",
    "rohs-konform": "RoHS Status",
    "rohs konform": "RoHS Status",
    "rohs": "RoHS Status",
    "bleifrei": "Lead-Free Status",
    "bleifrei-status": "Lead-Free Status",
    "bleifreier status": "Lead-Free Status",
    "halogenfrei": "Halogen-Free Status",
    "halogenfrei-status": "Halogen-Free Status",
    "halogenfreier status": "Halogen-Free Status",
    "breite": "Physical Width",
    "baubreite": "Physical Width",
    "länge": "Physical Length",
    "laenge": "Physical Length",
    "baulänge": "Physical Length",
    "baulaenge": "Physical Length",
    "höhe": "Physical Height",
    "hoehe": "Physical Height",
    "bauhöhe": "Physical Height",
    "bauhoehe": "Physical Height",
    "max. höhe": "Physical Height",
    "max. hoehe": "Physical Height",
    "gewicht": "Weight",
    "eigengewicht": "Weight",
    "stückgewicht": "Weight",
    "stueckgewicht": "Weight",
    "farbe": "Color",
    "led-farbe": "Color",
    "gehäusefarbe": "Color",
    "material": "Material",
    "gehäusematerial": "Material",
    "gehaeusematerial": "Material",
    "werkstoff": "Material",
    "körpermaterial": "Material",
    "koerpermaterial": "Material",
    "befestigungslochdurchmesser": "Mounting Hole Diameter",
    "bohrungsdurchmesser": "Mounting Hole Diameter",
    "montagebohrung": "Mounting Hole Diameter",
    # ════════════════════════════════════════════════════════════════
    # NEU: Kategorien, die im Original fehlten, bei Distributoren aber
    # (v.a. bei Facettensuche/Attributlisten von RS, Farnell, Digi-Key)
    # regelmäßig als eigenes Attribut auftauchen.
    # ════════════════════════════════════════════════════════════════
    "produktart": "Product Type",
    "produkttyp": "Product Type",
    "bauteiltyp": "Product Type",
    "warengruppe": "Product Type",
    "kategorie": "Product Type",
    "verpackungsart": "Packaging Type",
    "lieferform": "Packaging Type",
    "verpackungsform": "Packaging Type",
    "gurtung": "Packaging Type",
    "bandware": "Packaging Type",
    "normen/zulassungen": "Standards / Approvals",
    "normen / zulassungen": "Standards / Approvals",
    "zulassungen": "Standards / Approvals",
    "normen": "Standards / Approvals",
    "zertifizierungen": "Standards / Approvals",
    "zertifikate": "Standards / Approvals",
    "konformität": "Standards / Approvals",
    "konformitaet": "Standards / Approvals",
    "automobilstandard": "Automotive Qualification",
    "automotive qualifiziert": "Automotive Qualification",
    "aec-q qualifiziert": "Automotive Qualification",
    "automobiltauglich": "Automotive Qualification",
    "kfz-qualifiziert": "Automotive Qualification",
    "anschlussdurchmesser": "Lead Diameter",
    "drahtdurchmesser": "Lead Diameter",
    "anschlussdrahtdurchmesser": "Lead Diameter",
    "beindurchmesser": "Lead Diameter",
    "serie": "Series",
    "produktserie": "Series",
    "produktfamilie": "Series",
    "baureihe": "Series",
    "hersteller": "Manufacturer",
    "herstellername": "Manufacturer",
    "marke": "Manufacturer",
    "herstellernummer": "Manufacturer Part Number",
    "hersteller-teilenummer": "Manufacturer Part Number",
    "hersteller-artikelnummer": "Manufacturer Part Number",
    "mpn": "Manufacturer Part Number",
    "lebenszyklusstatus": "Lifecycle Status",
    "produktstatus": "Lifecycle Status",
    "verfügbarkeitsstatus": "Lifecycle Status",
    "verfuegbarkeitsstatus": "Lifecycle Status",
    "mindestbestellmenge": "Minimum Order Quantity",
    "mindestabnahmemenge": "Minimum Order Quantity",
    "mbm": "Minimum Order Quantity",
    "moq": "Minimum Order Quantity",
    "verpackungseinheit": "Order Multiple",
    "staffelmenge": "Order Multiple",
    "bestellmengenvielfaches": "Order Multiple",
    "lieferzeit": "Lead Time",
    "lieferfrist": "Lead Time",
    "datenblatt": "Datasheet URL",
    "datenblatt-url": "Datasheet URL",
    "datenblattlink": "Datasheet URL",
    "produktbeschreibung": "Part Description",
    "artikelbeschreibung": "Part Description",
    "beschreibung": "Part Description",
    "verfügbarkeit": "Availability",
    "verfuegbarkeit": "Availability",
    "lagerbestand": "Availability",
    "bestand": "Availability",
    "vorrätig": "Availability",
    "vorraetig": "Availability",
}

# -----------------------------------------------------------------------------
#  Noise words --- ignored during tokenisation
# -----------------------------------------------------------------------------
_STOPWORDS: Set[str] = {
    "and",
    "or",
    "the",
    "a",
    "an",
    "of",
    "for",
    "in",
    "to",
    "with",
    "by",
    "on",
    "at",
    "from",
    "as",
    "is",
    "are",
    "other",
    "general",
    "misc",
    "various",
    "smd",
    "smt",
    "thru",
    "hole",
    "through",
    "surface",
    "mount",
    "package",
    "type",
    "series",
    "standard",
}


# -----------------------------------------------------------------------------
#  Category cache
# -----------------------------------------------------------------------------


def get_category_tree() -> List[Dict]:
    """Fetch all InvenTree categories as a flat list with full paths."""
    global _category_cache_flat

    if _category_cache_flat is not None:
        return _category_cache_flat

    try:
        from part.models import PartCategory

        categories = PartCategory.objects.all()

        flat = []
        for cat in categories:
            path_parts = []
            current = cat
            while current is not None:
                path_parts.insert(0, current.name)
                current = current.parent

            flat.append(
                {
                    "id": cat.pk,
                    "name": cat.name,
                    "full_path": " > ".join(path_parts),
                    "path_parts": path_parts,
                    "level": (
                        cat.level if hasattr(cat, "level") else len(path_parts) - 1
                    ),
                    # Expose structural flag so callers never assign parts to
                    # structural (group-header) categories.
                    "structural": bool(getattr(cat, "structural", False)),
                    # Keep a reference for child-descent resolution
                    "_parent_id": cat.parent_id,
                }
            )

        _category_cache_flat = flat
        logger.debug(f"Loaded {len(flat)} InvenTree categories")
        return flat

    except Exception as e:
        logger.error(f"Failed to load categories: {e}")
        return []


def clear_cache():
    """Clear the category cache (call after category changes)."""
    global _category_cache_flat
    _category_cache_flat = None


def get_all_categories_for_ui() -> List[Dict]:
    """Return all categories formatted for dropdown/select UI.

    Each entry includes a ``structural`` boolean so the frontend can render
    structural (group-header) categories as disabled non-selectable options.
    """
    categories = get_category_tree()
    return [
        {
            "id": c["id"],
            "name": c["full_path"],
            "structural": c.get("structural", False),
        }
        for c in sorted(categories, key=lambda x: x["full_path"])
    ]


# -----------------------------------------------------------------------------
#  Synonym handling
# -----------------------------------------------------------------------------


def parse_user_synonyms(json_str: str) -> Dict[str, str]:
    """
    Parse the user-supplied synonym JSON string into a normalised dict.

    Expected format (either of):
        {"MLCC": "Ceramic Capacitor", "MCU": "Microcontroller"}
        [{"from": "MLCC", "to": "Ceramic Capacitor"}, ...]

    Returns dict mapping lowercase source --- lowercase target.
    Invalid JSON is logged and silently ignored.
    """
    if not json_str or not json_str.strip():
        return {}
    try:
        raw = json.loads(json_str)
        if isinstance(raw, dict):
            return {
                k.lower().strip(): v.lower().strip() for k, v in raw.items() if k and v
            }
        if isinstance(raw, list):
            result = {}
            for item in raw:
                if isinstance(item, dict) and item.get("from") and item.get("to"):
                    result[item["from"].lower().strip()] = item["to"].lower().strip()
            return result
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning(f"[CategoryMapper] Could not parse user synonyms: {e}")
    return {}


def build_synonym_table(user_synonyms_json: str = "") -> Dict[str, str]:
    """Merge built-in synonyms with user-defined ones (user takes priority)."""
    table = dict(_BUILTIN_SYNONYMS)
    table.update(parse_user_synonyms(user_synonyms_json))
    return table


# -----------------------------------------------------------------------------
#  Tokenisation
# -----------------------------------------------------------------------------

_SEP_RE = re.compile(r"[>\\/|,_\-–—&+()\[\]\s]+")

# Multi-stage noise strippers applied to distributor category strings
# before tokenisation. Each regex removes a class of noise.
_STRIP_PARENS_RE = re.compile(r"\([^)]*\)")  # (ICs), (LDO), etc.
_STRIP_VALUES_RE = re.compile(  # component value specs
    r"\b\d+(?:\.\d+)?"  # number
    r"(?:pf|nf|uf|µf|mf|f"  # capacitance
    r"|ohm|kohm|mohm|ω|kω|mω"  # resistance
    r"|uh|mh|nh|h"  # inductance
    r"|v|kv|mv"  # voltage
    r"|a|ma|µa|ua"  # current
    r"|mhz|khz|ghz|hz"  # frequency
    r"|w|mw|kw"  # power
    r")\b",
    re.IGNORECASE,
)
_STRIP_PACKAGE_RE = re.compile(  # SMD package codes
    r"\b(?:0201|0402|0603|0805|1206|1210|1812|2010|2512"
    r"|sop\d*|soic\d*|qfp\d*|tqfp\d*|bga\d*|dfn\d*|qfn\d*"
    r"|dip\d*|pdip\d*|to-?\d+[a-z]*|sot-?\d+[a-z]*)\b",
    re.IGNORECASE,
)
_STRIP_LONE_NUMS_RE = re.compile(r"\b\d+\b")  # standalone numbers
_STRIP_MULTI_SPACE_RE = re.compile(r"\s{2,}")  # collapse whitespace


def _strip_category_noise(text: str) -> str:
    """
    Strip distributor-specific noise from a category string.

    Runs multiple passes to remove:
      1. Parenthetical abbreviations: (ICs), (LDO), (SMD)
      2. Component value specs: 10uF, 50V, 0.1ohm
      3. SMD package codes: 0805, SOIC8, QFN32
      4. Standalone numbers left over after stripping
      5. Excess whitespace
    """
    s = text
    s = _STRIP_PARENS_RE.sub(" ", s)
    s = _STRIP_VALUES_RE.sub(" ", s)
    s = _STRIP_PACKAGE_RE.sub(" ", s)
    s = _STRIP_LONE_NUMS_RE.sub(" ", s)
    s = _STRIP_MULTI_SPACE_RE.sub(" ", s)
    return s.strip()


def _normalise(text: str) -> str:
    """Lowercase and collapse separators into spaces."""
    return " ".join(_SEP_RE.split(text.lower())).strip()


def _tokenise(text: str, synonyms: Dict[str, str]) -> List[str]:
    """
    Convert text to a de-duplicated token list with synonym expansion.

    Multi-word synonyms are matched greedily before single-word expansion,
    so "Ceramic Capacitor" --- "mlcc" works even when the key is multi-word.
    """
    norm = _normalise(text)

    # Greedy multi-word synonym replacement (longest key first)
    for src in sorted(synonyms, key=len, reverse=True):
        if " " in src and src in norm:
            norm = norm.replace(src, synonyms[src])

    words = norm.split()

    # Single-word synonym expansion
    expanded: List[str] = []
    for w in words:
        if w in synonyms:
            expanded.extend(synonyms[w].split())
        else:
            expanded.append(w)

    # Filter stopwords and very short tokens, de-duplicate preserving order
    seen: Set[str] = set()
    result: List[str] = []
    for w in expanded:
        if len(w) >= 2 and w not in _STOPWORDS and w not in seen:
            seen.add(w)
            result.append(w)

    return result


def _leaf_tokens(path_parts: List[str], synonyms: Dict[str, str]) -> List[str]:
    """
    Return a weighted token list that gives the leaf node 3-- weight
    and the second-to-last node 1.5-- weight.
    """
    tokens: List[str] = []
    n = len(path_parts)
    for i, part in enumerate(path_parts):
        pts = _tokenise(part, synonyms)
        depth = i - (n - 1)  # 0 for leaf, -1 for parent, etc.
        if depth == 0:
            tokens.extend(pts * 3)  # leaf --- 3-- weight
        elif depth == -1:
            tokens.extend(pts * 2)  # parent --- 2-- weight
        else:
            tokens.extend(pts)  # ancestors --- 1-- weight
    return tokens


def _dice(tokens_a: List[str], tokens_b: List[str]) -> float:
    """Sorensen-Dice coefficient between two token lists (0.0-1.0)."""
    if not tokens_a or not tokens_b:
        return 0.0
    set_a = set(tokens_a)
    set_b = set(tokens_b)
    intersection = set_a & set_b
    return (2.0 * len(intersection)) / (len(set_a) + len(set_b))


def fuzzy_match_category(
    distributor_category: str,
    threshold: int = 60,
    default_category_name: str = "",
    user_synonyms_json: str = "",
    learned_mappings_json: str = "",
) -> Tuple[Optional[int], str, int]:
    """
    Find the best matching InvenTree category for a distributor category string.

    Args:
        distributor_category:  Category string from distributor API
                               (e.g., "Semiconductors > Voltage Regulators")
        threshold:             Minimum confidence score (0-100) to accept.
        default_category_name: Fallback category name if no match found.
        user_synonyms_json:    JSON string of user-defined synonyms from settings.
        learned_mappings_json: JSON string of learned mappings from settings.
                               Keys are exact distributor category strings;
                               values are InvenTree category path strings.
                               A match here returns 100% confidence immediately.

    Returns:
        Tuple of (category_id, category_path, confidence_score)
        category_id is None if no match found and no default exists.
    """
    if not distributor_category:
        return _get_default_category(default_category_name)

    # Defensive coercion: threshold may arrive as None, str, or int
    try:
        threshold = int(threshold) if threshold is not None else 60
    except (ValueError, TypeError):
        threshold = 60

    # -- Step 0: Check Learned Mappings (absolute override) --
    if learned_mappings_json:
        try:
            learned: Dict[str, str] = json.loads(learned_mappings_json)
            if isinstance(learned, dict) and distributor_category in learned:
                target_path = learned[distributor_category]
                target_norm = _normalize_path(target_path)
                # Resolve to an actual category ID (separator-independent comparison)
                categories = get_category_tree()
                for cat in categories:
                    if (
                        _normalize_path(cat["full_path"]) == target_norm
                        or _normalize_path(cat["name"]) == target_norm
                    ):
                        logger.info(
                            f"Category matched via Learned Mapping: "
                            f"'{distributor_category}' -> '{cat['full_path']}'"
                        )
                        return (cat["id"], cat["full_path"], 100)
                # Path stored but category no longer exists -- log and continue
                logger.warning(
                    f"Learned mapping for '{distributor_category}' points to "
                    f"'{target_path}' which was not found in InvenTree categories. "
                    f"Falling back to fuzzy match."
                )
        except (json.JSONDecodeError, Exception) as e:
            logger.debug(f"Could not parse learned_mappings_json: {e}")

    categories = get_category_tree()
    if not categories:
        return _get_default_category(default_category_name)

    synonyms = build_synonym_table(user_synonyms_json)

    # Parse the distributor category into path segments and strip noise
    dist_parts_raw = [
        p.strip() for p in re.split(r"[>/|]", distributor_category) if p.strip()
    ]
    dist_parts = [_strip_category_noise(p) for p in dist_parts_raw]
    dist_parts = [
        p for p in dist_parts if p
    ]  # drop segments that became empty after stripping

    if not dist_parts:
        dist_parts = (
            dist_parts_raw  # fallback: use original if stripping killed everything
        )

    dist_tokens_flat = _tokenise(" ".join(dist_parts), synonyms)
    dist_tokens_leafw = _leaf_tokens(dist_parts, synonyms)
    dist_leaf = dist_parts[-1] if dist_parts else distributor_category
    dist_leaf_tokens = _tokenise(dist_leaf, synonyms)

    best_score = 0.0
    best_match = None

    for cat in categories:
        inv_parts = cat["path_parts"]
        inv_tokens_flat = _tokenise(" ".join(inv_parts), synonyms)
        inv_tokens_leafw = _leaf_tokens(inv_parts, synonyms)
        inv_leaf = inv_parts[-1] if inv_parts else cat["name"]
        inv_leaf_tokens = _tokenise(inv_leaf, synonyms)

        # -- Signal 1: whole-path token overlap (order-independent) ------
        sig_path = _dice(dist_tokens_flat, inv_tokens_flat)

        # -- Signal 2: leaf-weighted token overlap -----------------------
        sig_leaf_w = _dice(dist_tokens_leafw, inv_tokens_leafw)

        # -- Signal 3: leaf-only direct overlap --------------------------
        sig_leaf = _dice(dist_leaf_tokens, inv_leaf_tokens)

        # -- Signal 4: prefix / subset bonus -----------------------------
        #  "Ceramic Capacitors" IS a subset of the leaf tokens
        dl = set(dist_leaf_tokens)
        il = set(inv_leaf_tokens)
        prefix_bonus = 0.0
        if dl and il:
            if dl.issubset(il) or il.issubset(dl):
                prefix_bonus = 1.0
            elif dl & il:
                prefix_bonus = len(dl & il) / max(len(dl), len(il))

        # -- Combine signals ---------------------------------------------
        # 40% path, 30% leaf-weighted, 20% leaf-direct, 10% prefix bonus
        score = (
            0.40 * sig_path + 0.30 * sig_leaf_w + 0.20 * sig_leaf + 0.10 * prefix_bonus
        ) * 100.0

        if score > best_score:
            best_score = score
            best_match = cat

    if best_match and best_score >= threshold:
        # ── Structural category guard ────────────────────────────────────────
        # InvenTree forbids assigning parts to structural categories.  If the
        # best match is structural, attempt to descend into a non-structural
        # child; if that fails, force manual selection (return None).
        if best_match.get("structural"):
            resolved = _resolve_non_structural_child(
                best_match,
                categories,
                synonyms,
                dist_tokens_flat,
                dist_leaf_tokens,
                threshold,
            )
            if resolved:
                logger.info(
                    f"Category: structural '{best_match['full_path']}' resolved to "
                    f"non-structural child '{resolved['full_path']}' "
                    f"(score: {best_score:.1f})"
                )
                return (resolved["id"], resolved["full_path"], int(best_score))
            else:
                logger.info(
                    f"Category match landed on structural '{best_match['full_path']}' "
                    f"(score: {best_score:.1f}) – no non-structural child resolved; "
                    f"forcing manual selection."
                )
                return (None, "", 0)

        logger.info(
            f"Category match: '{distributor_category}' --- "
            f"'{best_match['full_path']}' (score: {best_score:.1f})"
        )
        return (best_match["id"], best_match["full_path"], int(best_score))

    logger.info(
        f"No category match for '{distributor_category}' "
        f"(best: {best_score:.1f}, threshold: {threshold})"
    )
    return _get_default_category(default_category_name)


# -----------------------------------------------------------------------------
#  Helpers
# -----------------------------------------------------------------------------


def _resolve_non_structural_child(
    structural_cat: Dict,
    all_categories: List[Dict],
    synonyms: Dict[str, str],
    dist_tokens_flat: List[str],
    dist_leaf_tokens: List[str],
    threshold: int,
) -> Optional[Dict]:
    """
    Attempt to descend from a structural parent category into its best-matching
    non-structural child using distributor token signals.

    The scoring uses a relaxed threshold (``threshold // 2``, minimum 20) so
    that even a moderately confident signal can disambiguate mounting type or
    sub-family (e.g. "SMD" vs "Through-Hole" when the distributor says "SMD").

    Args:
        structural_cat:    The structural category dict from the cache.
        all_categories:    Full flat category list from ``get_category_tree()``.
        synonyms:          Merged synonym table.
        dist_tokens_flat:  Flat distributor token list.
        dist_leaf_tokens:  Leaf-only distributor token list.
        threshold:         Original match threshold (used to derive relaxed value).

    Returns:
        Best matching non-structural child dict, or ``None`` if no confident
        child can be identified.
    """
    parent_id = structural_cat["id"]
    relaxed = max(threshold // 2, 20)

    # Direct children of the structural category that are themselves not structural
    children = [
        c
        for c in all_categories
        if c.get("_parent_id") == parent_id and not c.get("structural", False)
    ]

    if not children:
        return None

    best_score = 0.0
    best_child = None

    for child in children:
        inv_parts = child["path_parts"]
        inv_tokens_flat = _tokenise(" ".join(inv_parts), synonyms)
        inv_leaf_tokens = _tokenise(
            inv_parts[-1] if inv_parts else child["name"], synonyms
        )

        sig_path = _dice(dist_tokens_flat, inv_tokens_flat)
        sig_leaf = _dice(dist_leaf_tokens, inv_leaf_tokens)

        # Simplified 2-signal score: path + leaf-direct (no leaf-weight needed
        # since we are already scoped to children of the structural parent)
        dl = set(dist_leaf_tokens)
        il = set(inv_leaf_tokens)
        prefix_bonus = 0.0
        if dl and il:
            if dl.issubset(il) or il.issubset(dl):
                prefix_bonus = 1.0
            elif dl & il:
                prefix_bonus = len(dl & il) / max(len(dl), len(il))

        score = (0.50 * sig_path + 0.35 * sig_leaf + 0.15 * prefix_bonus) * 100.0

        if score > best_score:
            best_score = score
            best_child = child

    if best_child and best_score >= relaxed:
        logger.debug(
            "_resolve_non_structural_child: '%s' -> '%s' (score %.1f >= relaxed %d)",
            structural_cat["full_path"],
            best_child["full_path"],
            best_score,
            relaxed,
        )
        return best_child

    logger.debug(
        "_resolve_non_structural_child: no confident child for '%s' "
        "(best %.1f < relaxed %d, children: %s)",
        structural_cat["full_path"],
        best_score,
        relaxed,
        [c["name"] for c in children],
    )
    return None


def _get_default_category(name: str) -> Tuple[Optional[int], str, int]:
    """Look up the default/fallback category by name."""
    if not name:
        return (None, "", 0)

    categories = get_category_tree()
    for cat in categories:
        if cat["name"].lower() == name.lower():
            return (cat["id"], cat["full_path"], 50)

    return (None, name, 0)


def _normalize_path(path: str) -> str:
    """
    Normalize a category path string for separator-independent comparison.

    Collapses all separator variants ('/', ' > ', '|', '\\') to a single
    canonical separator '>' and lowercases the result.  This lets stored
    paths like 'Capacitors/Ceramic' match InvenTree full_paths like
    'Capacitors > Ceramic'.
    """
    # Replace all common separator forms with '>'
    normalized = re.sub(r"\s*[>/|\\]\s*", ">", path.strip())
    return normalized.lower()


def learn_category_mapping(
    distributor_category: str,
    chosen_category_path: str,
    plugin,
) -> bool:
    """
    Persist a manual category correction into LEARNED_CATEGORY_MAPPINGS.

    Args:
        distributor_category:  The raw distributor category string
                               (e.g. "Semiconductors > Voltage Regulators").
        chosen_category_path:  The InvenTree category path the user actually chose
                               (e.g. "Power > LDO Regulators").
        plugin:                The live InvenTree plugin instance with get/set_setting.

    Returns:
        True on success, False on error.
    """
    if not distributor_category or not chosen_category_path:
        return False
    try:
        current_json = plugin.get_setting("LEARNED_CATEGORY_MAPPINGS") or "{}"
        mappings: Dict[str, str] = json.loads(current_json)
        if not isinstance(mappings, dict):
            mappings = {}

        if mappings.get(distributor_category) == chosen_category_path:
            logger.debug(
                f"Learned mapping already correct: '{distributor_category}' --- '{chosen_category_path}'"
            )
            return True  # already up-to-date

        mappings[distributor_category] = chosen_category_path
        plugin.set_setting(
            "LEARNED_CATEGORY_MAPPINGS", json.dumps(mappings, ensure_ascii=False)
        )
        logger.info(
            f"Learned category mapping saved: '{distributor_category}' --- '{chosen_category_path}'"
        )
        return True
    except Exception as e:
        logger.error(f"Failed to save learned category mapping: {e}", exc_info=True)
        return False


# -----------------------------------------------------------------------------
#  Debug helper (call from a shell to tune thresholds)
# -----------------------------------------------------------------------------


def debug_match(
    distributor_category: str, top_n: int = 5, user_synonyms_json: str = ""
) -> List[Dict]:
    """
    Return the top-N scored candidates for a distributor category string.
    Useful for tuning thresholds from a Django shell.

    Usage:
        from inventree_smart_parts.services.category_mapper import debug_match
        debug_match("Semiconductors > Power > LDO Regulators")
    """
    categories = get_category_tree()
    synonyms = build_synonym_table(user_synonyms_json)
    dist_parts_raw = [
        p.strip() for p in re.split(r"[>/|]", distributor_category) if p.strip()
    ]
    dist_parts = [_strip_category_noise(p) for p in dist_parts_raw]
    dist_parts = [p for p in dist_parts if p] or dist_parts_raw
    dist_tokens_flat = _tokenise(" ".join(dist_parts), synonyms)
    dist_tokens_leafw = _leaf_tokens(dist_parts, synonyms)
    dist_leaf_tokens = _tokenise(
        dist_parts[-1] if dist_parts else distributor_category, synonyms
    )

    results = []
    for cat in categories:
        inv_parts = cat["path_parts"]
        inv_tokens_flat = _tokenise(" ".join(inv_parts), synonyms)
        inv_tokens_leafw = _leaf_tokens(inv_parts, synonyms)
        inv_leaf_tokens = _tokenise(
            inv_parts[-1] if inv_parts else cat["name"], synonyms
        )

        s1 = _dice(dist_tokens_flat, inv_tokens_flat)
        s2 = _dice(dist_tokens_leafw, inv_tokens_leafw)
        s3 = _dice(dist_leaf_tokens, inv_leaf_tokens)
        dl = set(dist_leaf_tokens)
        il = set(inv_leaf_tokens)
        pb = (
            1.0
            if (dl and il and (dl.issubset(il) or il.issubset(dl)))
            else (len(dl & il) / max(len(dl), len(il)) if (dl & il) else 0.0)
        )
        score = (0.40 * s1 + 0.30 * s2 + 0.20 * s3 + 0.10 * pb) * 100

        results.append(
            {
                "path": cat["full_path"],
                "score": round(score, 1),
                "sig_path": round(s1 * 100, 1),
                "sig_leaf_w": round(s2 * 100, 1),
                "sig_leaf": round(s3 * 100, 1),
                "prefix_bonus": round(pb * 100, 1),
            }
        )

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_n]
