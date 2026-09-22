## ADDED Requirements

### Requirement: Authorised vehicle registry
The system SHALL maintain a registry of vehicles authorised to pass the gate. Each entry SHALL store a normalised plate number (unique), a display plate number as entered, owner name, optional owner phone, optional department, vehicle type, an optional validity window (`valid_from`, `valid_until`), an active flag, and creation/update timestamps.

#### Scenario: Creating a registry entry
- **WHEN** an operator creates a vehicle entry with plate `30A-123.45`
- **THEN** the system SHALL store `plate_display` as `30A-123.45` and `plate_normalized` as `30A12345`, and the entry SHALL be active by default

#### Scenario: Duplicate normalised plate rejected
- **WHEN** an operator creates a vehicle whose normalised plate equals an existing entry's normalised plate
- **THEN** the system SHALL reject the creation with a validation error naming the conflicting entry

#### Scenario: Deactivating rather than deleting
- **WHEN** an operator revokes access for a vehicle
- **THEN** the entry SHALL be marked inactive and retained, so historical access events keep their vehicle reference

### Requirement: Plate normalisation
The system SHALL normalise every plate string — whether typed by an operator or read by OCR — through a single deterministic function before storage or comparison. Normalisation SHALL uppercase the string, remove separators (spaces, hyphens, dots, dashes of any width), and apply positional character coercion for Vietnamese plate layouts: characters at positions that must be digits SHALL be coerced from their confusable letters (`O`/`D`/`Q`→`0`, `I`/`L`/`T`→`1`, `Z`→`2`, `S`→`5`, `G`→`6`, `B`→`8`, `A`→`4`), and the series position SHALL be coerced in the reverse direction.

#### Scenario: Separator and case variants normalise identically
- **WHEN** the strings `30A-123.45`, `30a 12345` and `30A12345` are normalised
- **THEN** all three SHALL produce `30A12345`

#### Scenario: OCR confusable characters corrected positionally
- **WHEN** OCR returns `3OA-I2345` for a car plate
- **THEN** normalisation SHALL coerce `O`→`0` at digit position 2 and `I`→`1` at digit position 4, producing `30A12345`

#### Scenario: Motorbike series with trailing digit preserved
- **WHEN** the string `29X1-234.56` is normalised
- **THEN** the result SHALL be `29X123456` and the `X1` series SHALL NOT be coerced to digits

#### Scenario: Unrecognised layout passes through
- **WHEN** a string does not match any known Vietnamese plate layout
- **THEN** normalisation SHALL return the uppercased, separator-stripped string without positional coercion, and matching SHALL proceed on that value

### Requirement: Registry matching
The system SHALL match a normalised plate against the registry by exact equality on `plate_normalized`. A match SHALL be considered valid only when the entry is active and the current time falls inside its validity window.

#### Scenario: Active entry inside validity window
- **WHEN** a normalised plate exactly matches an active entry with `valid_from` in the past and `valid_until` null or in the future
- **THEN** the match SHALL be valid

#### Scenario: Expired entry
- **WHEN** a normalised plate exactly matches an entry whose `valid_until` is in the past
- **THEN** the match SHALL be invalid and the reason SHALL be `expired`

#### Scenario: Inactive entry
- **WHEN** a normalised plate exactly matches an entry with `is_active` false
- **THEN** the match SHALL be invalid and the reason SHALL be `inactive`

#### Scenario: Near miss is reported but never matched
- **WHEN** a normalised plate is at edit distance 1 from exactly one active registry entry and matches none exactly
- **THEN** the system SHALL record that entry as the near miss for operator review, and SHALL NOT treat it as a match

### Requirement: Registry management API
The system SHALL expose authenticated CRUD endpoints for the registry under `/api/v1/vehicles/`, supporting list with search by plate and owner, create, update, and deactivate.

#### Scenario: Unauthenticated access rejected
- **WHEN** an unauthenticated client requests any `/api/v1/vehicles/` endpoint
- **THEN** the system SHALL respond `403` and SHALL NOT disclose any registry contents
