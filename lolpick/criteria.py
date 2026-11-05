"""Criteri con cui viene valutato ogni campione (voti da 1 a 10)."""

CRITERIA = [
    ("physical", "Danni fisici"),
    ("magic", "Danni magici"),
    ("utility", "Utility"),
    ("lategame", "Late game"),
    ("earlygame", "Early game"),
    ("engage", "Hard engage"),
    ("cc", "CC"),
    ("disengage", "Disengage"),
    ("sustain", "Sustain"),
    ("tank", "Tank"),
    ("roaming", "Roaming"),
    ("mobility", "Mobilità"),
    ("burst", "Burst"),
    ("poke", "Poke"),
    ("dps", "DPS"),
    ("waveclear", "Waveclear"),
    ("splitpush", "Splitpush"),
    ("range", "Range"),  # portata effettiva delle minacce, abilità comprese (Xerath 10, Garen 1)
]

KEYS = [key for key, _ in CRITERIA]
LABELS = dict(CRITERIA)

# Nomi dei ruoli come li usa il client (assignedPosition).
ROLES = ["top", "jungle", "middle", "bottom", "utility"]
ROLE_LABELS = {"top": "Top", "jungle": "Jungle", "middle": "Mid", "bottom": "ADC", "utility": "Support", "": "?"}
ROLE_ALIASES = {
    "top": "top", "jungle": "jungle", "jng": "jungle", "jg": "jungle",
    "mid": "middle", "middle": "middle", "adc": "bottom", "bot": "bottom", "bottom": "bottom",
    "sup": "utility", "supp": "utility", "support": "utility", "utility": "utility",
}

# Archetipi di build (colonna "build" del CSV).
BUILDS = {
    "crit": ("Critico", "danno d'attacco, probabilità di critico, velocità d'attacco"),
    "onhit": ("On-hit / velocità d'attacco", "velocità d'attacco ed effetti on-hit"),
    "lethality": ("Letalità", "danno d'attacco e letalità: burst sui bersagli fragili"),
    "bruiser": ("Bruiser", "danno d'attacco, vita, un po' di resistenze, ability haste"),
    "tank": ("Tank", "vita, armatura, resistenza magica, oggetti con utility per la squadra"),
    "ap_burst": ("AP burst", "potere magico e penetrazione magica"),
    "ap_dps": ("AP DPS / battlemage", "potere magico, ability haste e danno sostenuto"),
    "ap_bruiser": ("AP bruiser", "potere magico insieme a vita e resistenze"),
    "enchanter": ("Enchanter", "potenziamento di cure e scudi, ability haste, mana"),
    "ad_caster": ("AD caster", "danno d'attacco, ability haste e mana"),
}
BUILD_TYPES = set(BUILDS)
