export interface CountryDialCode {
  code: string
  dial: string
  name: string
  flag: string
}

export interface NationalityOption {
  code: string
  name: string
  enName: string
  flag: string
}

export const COUNTRY_DIAL_CODES: CountryDialCode[] = [
  { code: 'VN', dial: '+84', name: 'Việt Nam', flag: '🇻🇳' },
  { code: 'DE', dial: '+49', name: 'Đức (Germany)', flag: '🇩🇪' },
  { code: 'US', dial: '+1', name: 'Hoa Kỳ (USA)', flag: '🇺🇸' },
  { code: 'JP', dial: '+81', name: 'Nhật Bản (Japan)', flag: '🇯🇵' },
  { code: 'KR', dial: '+82', name: 'Hàn Quốc (Korea)', flag: '🇰🇷' },
  { code: 'SG', dial: '+65', name: 'Singapore', flag: '🇸🇬' },
  { code: 'GB', dial: '+44', name: 'Vương Quốc Anh (UK)', flag: '🇬🇧' },
  { code: 'FR', dial: '+33', name: 'Pháp (France)', flag: '🇫🇷' },
  { code: 'AU', dial: '+61', name: 'Úc (Australia)', flag: '🇦🇺' },
  { code: 'TH', dial: '+66', name: 'Thái Lan', flag: '🇹🇭' },
  { code: 'CN', dial: '+86', name: 'Trung Quốc (China)', flag: '🇨🇳' },
  { code: 'TW', dial: '+886', name: 'Đài Loan (Taiwan)', flag: '🇹🇼' },
  { code: 'MY', dial: '+60', name: 'Malaysia', flag: '🇲🇾' },
  { code: 'CA', dial: '+1', name: 'Canada', flag: '🇨🇦' },
  { code: 'ID', dial: '+62', name: 'Indonesia', flag: '🇮🇩' },
  { code: 'CH', dial: '+41', name: 'Thụy Sĩ (Switzerland)', flag: '🇨🇭' },
  { code: 'RU', dial: '+7', name: 'Nga (Russia)', flag: '🇷🇺' },
]

export const NATIONALITIES: NationalityOption[] = [
  { code: 'VNM', name: 'Việt Nam', enName: 'Vietnam', flag: '🇻🇳' },
  { code: 'DEU', name: 'Đức', enName: 'Germany', flag: '🇩🇪' },
  { code: 'USA', name: 'Hoa Kỳ', enName: 'United States', flag: '🇺🇸' },
  { code: 'JPN', name: 'Nhật Bản', enName: 'Japan', flag: '🇯🇵' },
  { code: 'KOR', name: 'Hàn Quốc', enName: 'South Korea', flag: '🇰🇷' },
  { code: 'SGP', name: 'Singapore', enName: 'Singapore', flag: '🇸🇬' },
  { code: 'GBR', name: 'Vương Quốc Anh', enName: 'United Kingdom', flag: '🇬🇧' },
  { code: 'FRA', name: 'Pháp', enName: 'France', flag: '🇫🇷' },
  { code: 'AUS', name: 'Úc', enName: 'Australia', flag: '🇦🇺' },
  { code: 'THA', name: 'Thái Lan', enName: 'Thailand', flag: '🇹🇭' },
  { code: 'CHN', name: 'Trung Quốc', enName: 'China', flag: '🇨🇳' },
  { code: 'TWN', name: 'Đài Loan', enName: 'Taiwan', flag: '🇹🇼' },
  { code: 'MYS', name: 'Malaysia', enName: 'Malaysia', flag: '🇲🇾' },
  { code: 'CAN', name: 'Canada', enName: 'Canada', flag: '🇨🇦' },
  { code: 'IDN', name: 'Indonesia', enName: 'Indonesia', flag: '🇮🇩' },
  { code: 'CHE', name: 'Thụy Sĩ', enName: 'Switzerland', flag: '🇨🇭' },
  { code: 'RUS', name: 'Nga', enName: 'Russia', flag: '🇷🇺' },
  { code: 'PHL', name: 'Philippines', enName: 'Philippines', flag: '🇵🇭' },
  { code: 'IND', name: 'Ấn Độ', enName: 'India', flag: '🇮🇳' },
  { code: 'ITA', name: 'Ý', enName: 'Italy', flag: '🇮🇹' },
  { code: 'ESP', name: 'Tây Ban Nha', enName: 'Spain', flag: '🇪🇸' },
]

export function removeVietnameseTones(str: string): string {
  return str
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/đ/g, 'd')
    .replace(/Đ/g, 'D')
    .toLowerCase()
}

export function searchNationalities(query: string): NationalityOption[] {
  const clean = removeVietnameseTones(query.trim())
  if (!clean) return []
  return NATIONALITIES.filter((n) => {
    const nameMatch = removeVietnameseTones(n.name).includes(clean)
    const enNameMatch = removeVietnameseTones(n.enName).includes(clean)
    const codeMatch = n.code.toLowerCase().includes(clean)
    return nameMatch || enNameMatch || codeMatch
  }).slice(0, 8)
}
