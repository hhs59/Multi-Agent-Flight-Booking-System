export interface AirportOption {
  code: string
  city: string
  name: string
  country: string
  popular?: boolean
  keywords?: string[]
}

export const AIRPORT_DIRECTORY: AirportOption[] = [
  // --- VIỆT NAM (Nội địa) ---
  {
    code: 'SGN',
    city: 'TP. Hồ Chí Minh',
    name: 'Sân bay Quốc tế Tân Sơn Nhất',
    country: 'Việt Nam',
    popular: true,
    keywords: ['sai gon', 'saigon', 'hcm', 'tphcm', 'tan son nhat', 'mien nam'],
  },
  {
    code: 'HAN',
    city: 'Hà Nội',
    name: 'Sân bay Quốc tế Nội Bài',
    country: 'Việt Nam',
    popular: true,
    keywords: ['noi bai', 'thu do', 'mien bac'],
  },
  {
    code: 'DAD',
    city: 'Đà Nẵng',
    name: 'Sân bay Quốc tế Đà Nẵng',
    country: 'Việt Nam',
    popular: true,
    keywords: ['danang', 'hoi an', 'ba na', 'mien trung'],
  },
  {
    code: 'PQC',
    city: 'Phú Quốc',
    name: 'Sân bay Quốc tế Phú Quốc',
    country: 'Việt Nam',
    popular: true,
    keywords: ['phuquoc', 'kien giang', 'dao ngoc', 'an thoi', 'duong dong'],
  },
  {
    code: 'CXR',
    city: 'Nha Trang',
    name: 'Sân bay Quốc tế Cam Ranh',
    country: 'Việt Nam',
    popular: true,
    keywords: ['khanh hoa', 'cam ranh', 'nhatrang'],
  },
  {
    code: 'DLI',
    city: 'Đà Lạt',
    name: 'Sân bay Liên Khương',
    country: 'Việt Nam',
    popular: true,
    keywords: ['dalat', 'lam dong', 'lien khuong', 'tay nguyen'],
  },
  {
    code: 'HPH',
    city: 'Hải Phòng',
    name: 'Sân bay Quốc tế Cát Bi',
    country: 'Việt Nam',
    popular: true,
    keywords: ['haiphong', 'cat bi', 'ha long', 'cat ba'],
  },
  {
    code: 'HUI',
    city: 'Huế',
    name: 'Sân bay Quốc tế Phú Bài',
    country: 'Việt Nam',
    popular: true,
    keywords: ['hue', 'phu bai', 'thua thien'],
  },
  {
    code: 'VCA',
    city: 'Cần Thơ',
    name: 'Sân bay Quốc tế Cần Thơ',
    country: 'Việt Nam',
    popular: true,
    keywords: ['can tho', 'tra noc', 'mien tay', 'dong bang song cuu long'],
  },
  {
    code: 'VII',
    city: 'Vinh',
    name: 'Sân bay Quốc tế Vinh',
    country: 'Việt Nam',
    popular: true,
    keywords: ['nghe an', 'vinh', 'cua lo'],
  },
  {
    code: 'UIH',
    city: 'Quy Nhơn',
    name: 'Sân bay Phù Cát',
    country: 'Việt Nam',
    popular: true,
    keywords: ['binh dinh', 'phu cat', 'quynhon', 'ky co', 'eo gio'],
  },
  {
    code: 'THD',
    city: 'Thanh Hóa',
    name: 'Sân bay Thọ Xuân',
    country: 'Việt Nam',
    keywords: ['tho xuan', 'thanh hoa', 'sam son'],
  },
  {
    code: 'VDO',
    city: 'Quảng Ninh',
    name: 'Sân bay Quốc tế Vân Đồn',
    country: 'Việt Nam',
    popular: true,
    keywords: ['van don', 'ha long', 'quang ninh', 'bai chay'],
  },
  {
    code: 'VCS',
    city: 'Côn Đảo',
    name: 'Sân bay Cỏ Ống',
    country: 'Việt Nam',
    popular: true,
    keywords: ['con dao', 'co ong', 'ba ria vung tau'],
  },
  {
    code: 'BMV',
    city: 'Buôn Ma Thuột',
    name: 'Sân bay Buôn Ma Thuột',
    country: 'Việt Nam',
    keywords: ['dak lak', 'tay nguyen', 'buon me thuot'],
  },
  {
    code: 'PXU',
    city: 'Pleiku',
    name: 'Sân bay Pleiku',
    country: 'Việt Nam',
    keywords: ['gia lai', 'pleiku', 'bien ho'],
  },
  {
    code: 'TBB',
    city: 'Tuy Hòa',
    name: 'Sân bay Tuy Hòa',
    country: 'Việt Nam',
    keywords: ['phu yen', 'dong tac', 'tuy hoa', 'genh da dia'],
  },
  {
    code: 'DIN',
    city: 'Điện Biên Phủ',
    name: 'Sân bay Điện Biên',
    country: 'Việt Nam',
    keywords: ['dien bien', 'dien bien phu', 'tay bac'],
  },
  {
    code: 'VKG',
    city: 'Rạch Giá',
    name: 'Sân bay Rạch Giá',
    country: 'Việt Nam',
    keywords: ['kien giang', 'rach gia'],
  },
  {
    code: 'CAH',
    city: 'Cà Mau',
    name: 'Sân bay Cà Mau',
    country: 'Việt Nam',
    keywords: ['ca mau', 'dat mui'],
  },
  {
    code: 'VDH',
    city: 'Đồng Hới',
    name: 'Sân bay Đồng Hới',
    country: 'Việt Nam',
    keywords: ['quang binh', 'phong nha', 'ke bang', 'dong hoi'],
  },
  {
    code: 'VCL',
    city: 'Chu Lai',
    name: 'Sân bay Chu Lai',
    country: 'Việt Nam',
    keywords: ['quang nam', 'quang ngai', 'chu lai', 'dung quat', 'ly son'],
  },

  // --- QUỐC TẾ (Châu Á & Toàn cầu) ---
  {
    code: 'BKK',
    city: 'Bangkok',
    name: 'Sân bay Suvarnabhumi',
    country: 'Thái Lan',
    popular: true,
    keywords: ['thailand', 'thai lan', 'suvarnabhumi'],
  },
  {
    code: 'DMK',
    city: 'Bangkok',
    name: 'Sân bay Don Mueang',
    country: 'Thái Lan',
    keywords: ['thailand', 'thai lan', 'don mueang'],
  },
  {
    code: 'SIN',
    city: 'Singapore',
    name: 'Sân bay Quốc tế Changi',
    country: 'Singapore',
    popular: true,
    keywords: ['singapore', 'changi', 'sing'],
  },
  {
    code: 'KUL',
    city: 'Kuala Lumpur',
    name: 'Sân bay Quốc tế Kuala Lumpur (KLIA)',
    country: 'Malaysia',
    popular: true,
    keywords: ['malaysia', 'klia'],
  },
  {
    code: 'ICN',
    city: 'Seoul',
    name: 'Sân bay Quốc tế Incheon',
    country: 'Hàn Quốc',
    popular: true,
    keywords: ['korea', 'han quoc', 'incheon', 'seoul'],
  },
  {
    code: 'PUS',
    city: 'Busan',
    name: 'Sân bay Quốc tế Gimhae',
    country: 'Hàn Quốc',
    keywords: ['korea', 'han quoc', 'busan'],
  },
  {
    code: 'NRT',
    city: 'Tokyo',
    name: 'Sân bay Quốc tế Narita',
    country: 'Nhật Bản',
    popular: true,
    keywords: ['japan', 'nhat ban', 'narita', 'tokyo'],
  },
  {
    code: 'HND',
    city: 'Tokyo',
    name: 'Sân bay Quốc tế Haneda',
    country: 'Nhật Bản',
    popular: true,
    keywords: ['japan', 'nhat ban', 'haneda', 'tokyo'],
  },
  {
    code: 'KIX',
    city: 'Osaka',
    name: 'Sân bay Quốc tế Kansai',
    country: 'Nhật Bản',
    popular: true,
    keywords: ['japan', 'nhat ban', 'osaka', 'kyoto', 'kansai'],
  },
  {
    code: 'TPE',
    city: 'Đài Bắc',
    name: 'Sân bay Quốc tế Đào Viên (Taoyuan)',
    country: 'Đài Loan',
    popular: true,
    keywords: ['taiwan', 'dai loan', 'taipei', 'dao vien'],
  },
  {
    code: 'HKG',
    city: 'Hồng Kông',
    name: 'Sân bay Quốc tế Hong Kong',
    country: 'Hồng Kông',
    popular: true,
    keywords: ['hong kong', 'hongkong', 'huong cang'],
  },
  {
    code: 'CNX',
    city: 'Chiang Mai',
    name: 'Sân bay Chiang Mai',
    country: 'Thái Lan',
    keywords: ['thailand', 'chiangmai'],
  },
  {
    code: 'HKT',
    city: 'Phuket',
    name: 'Sân bay Quốc tế Phuket',
    country: 'Thái Lan',
    keywords: ['thailand', 'phuket'],
  },
  {
    code: 'DPS',
    city: 'Bali',
    name: 'Sân bay Quốc tế Ngurah Rai',
    country: 'Indonesia',
    popular: true,
    keywords: ['indonesia', 'bali', 'denpasar'],
  },
  {
    code: 'MNL',
    city: 'Manila',
    name: 'Sân bay Quốc tế Ninoy Aquino',
    country: 'Philippines',
    keywords: ['philippines', 'manila'],
  },
  {
    code: 'PEK',
    city: 'Bắc Kinh',
    name: 'Sân bay Quốc tế Thủ Đô Bắc Kinh',
    country: 'Trung Quốc',
    keywords: ['china', 'trung quoc', 'beijing', 'bac kinh'],
  },
  {
    code: 'PKX',
    city: 'Bắc Kinh',
    name: 'Sân bay Quốc tế Đại Hưng',
    country: 'Trung Quốc',
    keywords: ['china', 'daxing'],
  },
  {
    code: 'PVG',
    city: 'Thượng Hải',
    name: 'Sân bay Quốc tế Phố Đông (Pudong)',
    country: 'Trung Quốc',
    keywords: ['china', 'trung quoc', 'shanghai', 'thuong hai'],
  },
  {
    code: 'CAN',
    city: 'Quảng Châu',
    name: 'Sân bay Quốc tế Bạch Vân (Baiyun)',
    country: 'Trung Quốc',
    keywords: ['guangzhou', 'china'],
  },
  {
    code: 'SYD',
    city: 'Sydney',
    name: 'Sân bay Kingsford Smith',
    country: 'Úc (Australia)',
    popular: true,
    keywords: ['australia', 'uc', 'sydney'],
  },
  {
    code: 'MEL',
    city: 'Melbourne',
    name: 'Sân bay Tullamarine',
    country: 'Úc (Australia)',
    popular: true,
    keywords: ['australia', 'uc', 'melbourne'],
  },
  {
    code: 'LHR',
    city: 'London',
    name: 'Sân bay Heathrow',
    country: 'Vương Quốc Anh',
    popular: true,
    keywords: ['uk', 'england', 'anh', 'london', 'heathrow'],
  },
  {
    code: 'CDG',
    city: 'Paris',
    name: 'Sân bay Charles de Gaulle',
    country: 'Pháp (France)',
    popular: true,
    keywords: ['france', 'phap', 'paris'],
  },
  {
    code: 'FRA',
    city: 'Frankfurt',
    name: 'Sân bay Quốc tế Frankfurt',
    country: 'Đức (Germany)',
    keywords: ['germany', 'duc', 'frankfurt'],
  },
  {
    code: 'DXB',
    city: 'Dubai',
    name: 'Sân bay Quốc tế Dubai',
    country: 'UAE',
    popular: true,
    keywords: ['uae', 'emirates', 'dubai'],
  },
  {
    code: 'DOH',
    city: 'Doha',
    name: 'Sân bay Quốc tế Hamad',
    country: 'Qatar',
    keywords: ['qatar', 'doha'],
  },
  {
    code: 'JFK',
    city: 'New York',
    name: 'Sân bay Quốc tế JFK',
    country: 'Hoa Kỳ (USA)',
    popular: true,
    keywords: ['usa', 'my', 'america', 'new york', 'jfk'],
  },
  {
    code: 'LAX',
    city: 'Los Angeles',
    name: 'Sân bay Quốc tế Los Angeles',
    country: 'Hoa Kỳ (USA)',
    popular: true,
    keywords: ['california', 'usa', 'my', 'los angeles'],
  },
  {
    code: 'SFO',
    city: 'San Francisco',
    name: 'Sân bay Quốc tế San Francisco',
    country: 'Hoa Kỳ (USA)',
    keywords: ['california', 'usa', 'my', 'san francisco'],
  },
]

export function removeVietnameseTones(str: string): string {
  return str
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/đ/g, 'd')
    .replace(/Đ/g, 'D')
    .toLowerCase()
}

export function searchAirports(query: string): AirportOption[] {
  const clean = removeVietnameseTones(query.trim())
  if (!clean) {
    return AIRPORT_DIRECTORY.filter((a) => a.popular).slice(0, 8)
  }

  const exactCode = AIRPORT_DIRECTORY.filter((a) => a.code.toLowerCase() === clean)
  if (exactCode.length > 0) return exactCode

  return AIRPORT_DIRECTORY.filter((item) => {
    const codeMatch = item.code.toLowerCase().includes(clean)
    const cityMatch = removeVietnameseTones(item.city).includes(clean)
    const nameMatch = removeVietnameseTones(item.name).includes(clean)
    const countryMatch = removeVietnameseTones(item.country).includes(clean)
    const keywordMatch = item.keywords?.some((k) => removeVietnameseTones(k).includes(clean))

    return codeMatch || cityMatch || nameMatch || countryMatch || keywordMatch
  }).slice(0, 8)
}
