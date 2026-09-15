// Curated suburb/city list for the Location autocomplete.
//
// This is a static list, not a geocoding API — it's meant to get
// autocomplete working immediately without an external dependency or
// API key. Auckland suburbs are listed first since that's where the
// backend's product catalogue currently has store coverage (see the
// design doc: five Auckland zones, PAK'nSAVE + New World in each).
// Swap this for a real address/suburb API (e.g. LINZ or a places
// autocomplete service) if you need exhaustive NZ-wide coverage.

export const AUCKLAND_SUBURBS = [
  "Albany",
  "Browns Bay",
  "Takapuna",
  "Milford",
  "Glenfield",
  "Birkenhead",
  "Northcote",
  "Devonport",
  "Mt Eden",
  "Ponsonby",
  "Grey Lynn",
  "Newmarket",
  "Parnell",
  "Remuera",
  "Ellerslie",
  "Onehunga",
  "Mt Wellington",
  "Panmure",
  "Glen Innes",
  "Manukau",
  "Botany",
  "Howick",
  "Pakuranga",
  "Flat Bush",
  "Papatoetoe",
  "Otahuhu",
  "Mangere",
  "Airport Oaks",
  "Henderson",
  "Lincoln Road",
  "New Lynn",
  "Te Atatu",
  "West Harbour",
  "Massey",
  "Hobsonville",
  "Whangaparaoa",
  "Silverdale",
  "Orewa",
  "Warkworth",
  "Pukekohe",
  "Papakura",
  "Drury",
];

export const OTHER_NZ_CITIES = [
  "Whangarei",
  "Hamilton",
  "Tauranga",
  "Rotorua",
  "Gisborne",
  "Napier",
  "Hastings",
  "New Plymouth",
  "Whanganui",
  "Palmerston North",
  "Wellington",
  "Nelson",
  "Christchurch",
  "Timaru",
  "Queenstown",
  "Dunedin",
  "Invercargill",
];

export const NZ_LOCATIONS = [...AUCKLAND_SUBURBS, ...OTHER_NZ_CITIES];
