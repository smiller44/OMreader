_CITY_TO_MSA: dict[tuple[str, str], str] = {
    # California
    ("anaheim",          "CA"): "Anaheim-Santa Ana-Irvine",
    ("santa ana",        "CA"): "Anaheim-Santa Ana-Irvine",
    ("irvine",           "CA"): "Anaheim-Santa Ana-Irvine",
    ("los angeles",      "CA"): "L.A. - Long Beach-Glendale",
    ("long beach",       "CA"): "L.A. - Long Beach-Glendale",
    ("glendale",         "CA"): "L.A. - Long Beach-Glendale",
    ("burbank",          "CA"): "L.A. - Long Beach-Glendale",
    ("pasadena",         "CA"): "L.A. - Long Beach-Glendale",
    ("torrance",         "CA"): "L.A. - Long Beach-Glendale",
    ("inglewood",        "CA"): "L.A. - Long Beach-Glendale",
    ("compton",          "CA"): "L.A. - Long Beach-Glendale",
    ("san francisco",    "CA"): "San Francisco",
    ("san jose",         "CA"): "San Jose-Sunnyvale-S. Clara",
    ("sunnyvale",        "CA"): "San Jose-Sunnyvale-S. Clara",
    ("santa clara",      "CA"): "San Jose-Sunnyvale-S. Clara",
    ("cupertino",        "CA"): "San Jose-Sunnyvale-S. Clara",
    ("mountain view",    "CA"): "San Jose-Sunnyvale-S. Clara",
    ("palo alto",        "CA"): "San Jose-Sunnyvale-S. Clara",
    ("oakland",          "CA"): "Oakland-Hayward-Berkeley",
    ("hayward",          "CA"): "Oakland-Hayward-Berkeley",
    ("berkeley",         "CA"): "Oakland-Hayward-Berkeley",
    ("fremont",          "CA"): "Oakland-Hayward-Berkeley",
    ("san diego",        "CA"): "San Diego",
    ("chula vista",      "CA"): "San Diego",
    ("riverside",        "CA"): "Riverside-San Bernardino",
    ("san bernardino",   "CA"): "Riverside-San Bernardino",
    ("ontario",          "CA"): "Riverside-San Bernardino",
    ("moreno valley",    "CA"): "Riverside-San Bernardino",
    ("oxnard",           "CA"): "Oxnard-Thousand Oaks",
    ("thousand oaks",    "CA"): "Oxnard-Thousand Oaks",
    ("ventura",          "CA"): "Oxnard-Thousand Oaks",
    ("santa barbara",    "CA"): "Santa Maria-Santa Barbara",
    ("santa maria",      "CA"): "Santa Maria-Santa Barbara",
    ("santa rosa",       "CA"): "Santa Rosa",
    ("vallejo",          "CA"): "Vallejo/Fairfield/Napa",
    ("fairfield",        "CA"): "Vallejo/Fairfield/Napa",
    ("napa",             "CA"): "Vallejo/Fairfield/Napa",
    # Texas
    ("dallas",           "TX"): "Dallas-Plano-Irving",
    ("plano",            "TX"): "Dallas-Plano-Irving",
    ("irving",           "TX"): "Dallas-Plano-Irving",
    ("garland",          "TX"): "Dallas-Plano-Irving",
    ("mesquite",         "TX"): "Dallas-Plano-Irving",
    ("richardson",       "TX"): "Dallas-Plano-Irving",
    ("fort worth",       "TX"): "Ft. Worth-Arlington",
    ("arlington",        "TX"): "Ft. Worth-Arlington",
    ("houston",          "TX"): "Houston",
    ("austin",           "TX"): "Austin",
    ("san antonio",      "TX"): "San Antonio",
    # Florida
    ("miami",            "FL"): "Miami-Kendall",
    ("kendall",          "FL"): "Miami-Kendall",
    ("hialeah",          "FL"): "Miami-Kendall",
    ("doral",            "FL"): "Miami-Kendall",
    ("fort lauderdale",  "FL"): "Ft. Lauderdale-Pompano",
    ("pompano beach",    "FL"): "Ft. Lauderdale-Pompano",
    ("hollywood",        "FL"): "Ft. Lauderdale-Pompano",
    ("coral springs",    "FL"): "Ft. Lauderdale-Pompano",
    ("west palm beach",  "FL"): "West Palm-Boca-Delray",
    ("boca raton",       "FL"): "West Palm-Boca-Delray",
    ("delray beach",     "FL"): "West Palm-Boca-Delray",
    ("boynton beach",    "FL"): "West Palm-Boca-Delray",
    ("orlando",          "FL"): "Orlando",
    ("kissimmee",        "FL"): "Orlando",
    ("sanford",          "FL"): "Orlando",
    ("jacksonville",     "FL"): "Jacksonville",
    ("tampa",            "FL"): "Tampa-St. Pete",
    ("st. pete",         "FL"): "Tampa-St. Pete",
    ("st. petersburg",   "FL"): "Tampa-St. Pete",
    ("clearwater",       "FL"): "Tampa-St. Pete",
    ("palm bay",         "FL"): "Palm Bay-Melbourne",
    ("melbourne",        "FL"): "Palm Bay-Melbourne",
    ("naples",           "FL"): "Naples-Marco Island",
    ("marco island",     "FL"): "Naples-Marco Island",
    ("sarasota",         "FL"): "N. Port-Sarasota-Bradenton",
    ("bradenton",        "FL"): "N. Port-Sarasota-Bradenton",
    ("north port",       "FL"): "N. Port-Sarasota-Bradenton",
    # Georgia
    ("atlanta",          "GA"): "Atlanta",
    ("marietta",         "GA"): "Atlanta",
    ("savannah",         "GA"): "Atlanta",
    # Illinois
    ("chicago",          "IL"): "Chicago",
    ("aurora",           "IL"): "Chicago",
    ("naperville",       "IL"): "Chicago",
    # Colorado
    ("denver",           "CO"): "Denver",
    ("aurora",           "CO"): "Denver",
    ("lakewood",         "CO"): "Denver",
    ("boulder",          "CO"): "Boulder",
    # Arizona
    ("phoenix",          "AZ"): "Phoenix",
    ("scottsdale",       "AZ"): "Phoenix",
    ("tempe",            "AZ"): "Phoenix",
    ("mesa",             "AZ"): "Phoenix",
    ("chandler",         "AZ"): "Phoenix",
    ("gilbert",          "AZ"): "Phoenix",
    ("glendale",         "AZ"): "Phoenix",
    ("peoria",           "AZ"): "Phoenix",
    # Nevada
    ("las vegas",        "NV"): "Las Vegas",
    ("henderson",        "NV"): "Las Vegas",
    ("north las vegas",  "NV"): "Las Vegas",
    # Washington
    ("seattle",          "WA"): "Seattle",
    ("bellevue",         "WA"): "Seattle",
    ("redmond",          "WA"): "Seattle",
    ("tacoma",           "WA"): "Tacoma-Lakewood",
    ("lakewood",         "WA"): "Tacoma-Lakewood",
    # Oregon
    ("portland",         "OR"): "Portland",
    ("beaverton",        "OR"): "Portland",
    ("lake oswego",      "OR"): "Portland",
    ("gresham",          "OR"): "Portland",
    ("hillsboro",        "OR"): "Portland",
    ("tualatin",         "OR"): "Portland",
    ("tigard",           "OR"): "Portland",
    ("milwaukie",        "OR"): "Portland",
    ("happy valley",     "OR"): "Portland",
    ("west linn",        "OR"): "Portland",
    ("oregon city",      "OR"): "Portland",
    ("wilsonville",      "OR"): "Portland",
    # Utah
    ("salt lake city",   "UT"): "Salt Lake City",
    ("west valley city", "UT"): "Salt Lake City",
    ("provo",            "UT"): "Salt Lake City",
    # North Carolina
    ("charlotte",        "NC"): "Charlotte",
    ("raleigh",          "NC"): "Raleigh",
    ("durham",           "NC"): "Raleigh",
    ("cary",             "NC"): "Raleigh",
    # Tennessee
    ("nashville",        "TN"): "Nashville",
    ("memphis",          "TN"): "Nashville",
    # South Carolina
    ("charleston",       "SC"): "Charleston",
    ("greenville",       "SC"): "Greenville",
    # Virginia / DC area
    ("arlington",        "VA"): "Washington-Northern VA",
    ("alexandria",       "VA"): "Washington-Northern VA",
    ("falls church",     "VA"): "Washington-Northern VA",
    ("fairfax",          "VA"): "Washington-Northern VA",
    ("reston",           "VA"): "Washington-Northern VA",
    ("washington",       "DC"): "Washington-Northern VA",
    # Maryland
    ("baltimore",        "MD"): "Baltimore",
    # New York
    ("new york",         "NY"): "New York-White Plains",
    ("white plains",     "NY"): "New York-White Plains",
    ("yonkers",          "NY"): "New York-White Plains",
    ("bronx",            "NY"): "New York-White Plains",
    ("brooklyn",         "NY"): "New York-White Plains",
    ("queens",           "NY"): "New York-White Plains",
    ("staten island",    "NY"): "New York-White Plains",
    ("hempstead",        "NY"): "Nassau Co. - Suffolk Co.",
    ("brentwood",        "NY"): "Nassau Co. - Suffolk Co.",
    # New Jersey
    ("newark",           "NJ"): "Newark-Jersey City",
    ("jersey city",      "NJ"): "Newark-Jersey City",
    ("paterson",         "NJ"): "Newark-Jersey City",
    # Connecticut
    ("bridgeport",       "CT"): "Bridgeport-Stamford",
    ("stamford",         "CT"): "Bridgeport-Stamford",
    ("norwalk",          "CT"): "Bridgeport-Stamford",
    # Massachusetts
    ("boston",           "MA"): "Boston",
    ("worcester",        "MA"): "Worcester",
    ("springfield",      "MA"): "Boston",
    # Pennsylvania
    ("philadelphia",     "PA"): "Philadelphia",
    # Minnesota
    ("minneapolis",      "MN"): "Minneapolis-St. Paul",
    ("st. paul",         "MN"): "Minneapolis-St. Paul",
    ("saint paul",       "MN"): "Minneapolis-St. Paul",
    ("bloomington",      "MN"): "Minneapolis-St. Paul",
}


MSA_OPTIONS: list[str] = [
    "Anaheim-Santa Ana-Irvine", "Atlanta", "Austin", "Baltimore", "Boston",
    "Boulder", "Bridgeport-Stamford", "Charleston", "Charlotte", "Chicago",
    "Dallas-Plano-Irving", "Denver", "Ft. Lauderdale-Pompano", "Ft. Worth-Arlington",
    "Greenville", "Houston", "Jacksonville", "L.A. - Long Beach-Glendale",
    "Las Vegas", "Miami-Kendall", "Minneapolis-St. Paul", "N. Port-Sarasota-Bradenton",
    "Naples-Marco Island", "Nashville", "Nassau Co. - Suffolk Co.", "New York-White Plains",
    "Newark-Jersey City", "Oakland-Hayward-Berkeley", "Orlando", "Oxnard-Thousand Oaks",
    "Palm Bay-Melbourne", "Philadelphia", "Phoenix", "Portfolio", "Portland",
    "Raleigh", "Riverside-San Bernardino", "Salt Lake City", "San Antonio",
    "San Diego", "San Francisco", "San Jose-Sunnyvale-S. Clara",
    "Santa Maria-Santa Barbara", "Santa Rosa", "Seattle", "Tacoma-Lakewood",
    "Tampa-St. Pete", "Vallejo/Fairfield/Napa", "Washington-Northern VA",
    "West Palm-Boca-Delray", "Worcester",
]

COUNTY_OPTIONS: list[str] = [
    "Arapahoe", "Arlington", "Bergen", "Broward", "Charleston", "Chester",
    "Clark", "Cobb", "Collin", "Cook", "Dallas", "Davidson", "DeKalb",
    "Denton", "Denver", "Douglas", "DuPage", "Durham", "Durham/Orange",
    "Forsyth", "Fulton", "Gwinnett", "Harris", "Hennepin", "Hillsborough",
    "Iredell", "Jefferson", "Kane", "King", "Los Angeles", "Manatee",
    "Maricopa", "Marietta", "Martin", "Mecklenburg", "Miami-Dade", "Middlesex",
    "Multnomah", "Orange", "Osceola", "Palm Beach", "Pinellas", "Portfolio",
    "Prince William", "San Bernardino", "San Diego", "Seminole", "Suffolk",
    "Tarrant", "Travis", "Ventura", "Wake", "Williamson",
]

STATE_OPTIONS: list[str] = [
    "AZ", "CA", "CO", "CT", "DC", "FL", "GA", "IL", "MA", "MD",
    "MN", "NC", "NJ", "NV", "NY", "OR", "PA", "SC", "TN", "TX",
    "UT", "VA", "WA",
]

SUBMARKET_OPTIONS: list[str] = [
    "Addison/Bent Tree", "Airport Area", "Alamo Heights", "Alief",
    "Allen/McKinney", "Aloha/West Beaverton", "Alpharetta/Cumming",
    "Altamonte Springs/Apopka", "Anderson", "Annapolis", "Anoka County",
    "Antelope Valley", "Arboretum", "Arlington",
    "Arlington Heights/Palatine/Wheeling", "Arvada/Golden", "Aurora",
    "Avondale/Goodyear/West Glendale", "Ballantyne", "Baltimore City East",
    "Baltimore City North", "Baltimore City West", "Baymeadows", "Baytown",
    "Bear Creek", "Bergen County", "Bethesda/Chevy Chase", "Bloomington",
    "Boca Raton", "Boulder", "Boynton Beach/Delray Beach", "Bradenton",
    "Braeswood Place/Astrodome/South Union",
    "Brandon/Southeast Hillsborough County", "Brazoria County",
    "Brentwood/Westwood/Beverly Hills", "Briarcliff", "Bridgeport/Danbury",
    "Bronx", "Bronzeville/Hyde Park/South Shore", "Brooklyn", "Broomfield",
    "Buckhead", "Bucks County", "Buena Park/Cypress",
    "Burbank/Glendale/Pasadena", "Burleson/Johnson County",
    "Burlington County", "Burnsville/Apple Valley", "Camarillo",
    "Cambridge/Somerville",
]

TYPE_OPTIONS: list[str] = [
    "Garden / tbd", "Garden / Wood", "Garden / Concrete", "Garden / Steel",
    "Garden HD / tbd", "Garden HD / Wood", "Garden HD / Concrete", "Garden HD / Steel",
    "Mid-Rise Wrap / tbd", "Mid-Rise Wrap / Wood", "Mid-Rise Wrap / Concrete", "Mid-Rise Wrap / Steel",
    "Mid-Rise Podium / tbd", "Mid-Rise Podium / Wood", "Mid-Rise Podium / Concrete", "Mid-Rise Podium / Steel",
    "High-Rise / tbd", "High-Rise / Wood", "High-Rise / Concrete", "High-Rise / Steel",
    "Other",
]

BROKERAGE_OPTIONS: list[str] = [
    "Berkadia", "C&W", "CBRE", "Eastdil", "Engler", "IPA", "JLL",
    "Kidder Mathews", "Newmark", "Northmarq", "W&D", "W&D / Engler",
    "BlueGate", "Patterson", "Colliers", "Bravo & Partners", "none",
]


def msa_for_deal(deal: dict) -> str:
    """Return the MSA label for a deal dict, falling back to raw city name."""
    cs = deal.get("city_state", "")
    parts = cs.split(",")
    city  = parts[0].strip().lower()
    state = parts[1].strip().upper() if len(parts) > 1 else ""
    return _CITY_TO_MSA.get((city, state)) or cs.split(",")[0].strip().title() or "Other"
