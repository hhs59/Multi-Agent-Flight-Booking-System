import { chromium } from 'playwright'
import { spawn } from 'child_process'
import path from 'path'
import fs from 'fs'

const outputDir = path.resolve('public/screenshots')
if (!fs.existsSync(outputDir)) {
  fs.mkdirSync(outputDir, { recursive: true })
}

async function run() {
  console.log('Starting Vite dev server...')
  const vite = spawn('npx', ['vite', '--port', '5179'], {
    shell: true,
    cwd: path.resolve('.'),
    stdio: 'ignore',
  })

  // Give vite time to start and poll
  for (let i = 0; i < 15; i++) {
    try {
      const res = await fetch('http://localhost:5179')
      if (res.ok) {
        console.log('Vite dev server is ready!')
        break
      }
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 1000))
    }
  }

  console.log('Launching Playwright Chromium browser...')
  const browser = await chromium.launch({ headless: true })
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } })
  const page = await context.newPage()

  // Mock API endpoints to allow rendering pages without full backend
  await page.route('**/v1/auth/me', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        user_id: 'usr_student_01',
        email: 'student@university.edu',
        display_name: 'Student Developer',
        locale: 'vi',
        timezone: 'Asia/Ho_Chi_Minh',
      }),
    })
  })

  await page.route('**/v1/threads', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        threads: [
          {
            id: 'th_01',
            title: 'Tìm chuyến bay Hà Nội - Singapore',
            created_at: new Date().toISOString(),
            status: 'active',
          },
        ],
      }),
    })
  })

  await page.route('**/v1/threads/th_01/history', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        messages: [
          {
            id: 'msg_01',
            role: 'user',
            content: 'Tìm chuyến bay từ Hà Nội đi Singapore thứ Ba tuần sau dưới 5 triệu',
            created_at: new Date().toISOString(),
          },
          {
            id: 'msg_02',
            role: 'assistant',
            content:
              'Đã tìm thấy 3 chuyến bay phù hợp từ Hà Nội (HAN) đi Singapore (SIN) vào ngày 2026-08-25 với giá vé dưới 5.000.000 VND.',
            created_at: new Date().toISOString(),
            safe_result: {
              status: 'success',
              offers: [
                {
                  id: 'off_01',
                  airline_name: 'VietJet Air',
                  flight_number: 'VJ915',
                  price_amount: '3850000',
                  price_currency: 'VND',
                  departure_time: '2026-08-25T09:30:00+07:00',
                  arrival_time: '2026-08-25T13:50:00+08:00',
                  origin: 'HAN',
                  destination: 'SIN',
                  duration: '3h 20m',
                  stops: 0,
                },
                {
                  id: 'off_02',
                  airline_name: 'Vietnam Airlines',
                  flight_number: 'VN661',
                  price_amount: '4620000',
                  price_currency: 'VND',
                  departure_time: '2026-08-25T10:45:00+07:00',
                  arrival_time: '2026-08-25T15:10:00+08:00',
                  origin: 'HAN',
                  destination: 'SIN',
                  duration: '3h 25m',
                  stops: 0,
                },
              ],
            },
          },
        ],
      }),
    })
  })

  await page.route('**/v1/travelers', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        travelers: [
          {
            id: 'trv_01',
            label: 'Chính chủ',
            given_name: 'Nguyễn Văn',
            family_name: 'An',
            born_on: '1998-05-15',
            gender: 'm',
            email: 'nguyen.an@example.com',
            phone_number: '+84901234567',
            passport_number_masked: 'C123****',
            completeness: 'ready_international',
            is_default: true,
          },
        ],
      }),
    })
  })

  await page.route('**/v1/bookings', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        bookings: [
          {
            id: 'bk_01',
            status: 'order_created',
            provider_order_reference: 'ord_0000A89x',
            confirmation_code: 'HK79XY',
            total_amount: '3850000',
            currency: 'VND',
            created_at: new Date().toISOString(),
            offer_details: {
              airline_name: 'VietJet Air',
              flight_number: 'VJ915',
              origin: 'HAN',
              destination: 'SIN',
              departure_time: '2026-08-25T09:30:00+07:00',
            },
          },
        ],
      }),
    })
  })
  await page.route('**/v1/bookings/bk_01', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        id: 'bk_01',
        status: 'order_created',
        provider: 'Duffel Global Airline GDS',
        provider_environment: 'sandbox',
        masked_provider_order_reference: 'ord_0000A89x',
        confirmation_code: 'HK79XY',
        total_amount: '3850000',
        currency: 'VND',
        created_at: new Date().toISOString(),
        last_reconciled_at: new Date().toISOString(),
        offer_details: {
          airline_name: 'VietJet Air',
          flight_number: 'VJ915',
          origin: 'HAN',
          destination: 'SIN',
          departure_time: '2026-08-25T09:30:00+07:00',
        },
      }),
    })
  })

  await page.route('**/v1/watches', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        watches: [
          {
            id: 'wt_01',
            origin: 'HAN',
            destination: 'SIN',
            max_price: '4500000',
            currency: 'VND',
            status: 'active',
            last_checked_at: new Date().toISOString(),
          },
        ],
      }),
    })
  })

  // Set storage mock so sessionStore.read() succeeds
  await page.goto('http://localhost:5179/login')
  await page.evaluate(() => {
    const session = JSON.stringify({
      csrfToken: 'mock-csrf-token-12345',
      expiresAt: new Date(Date.now() + 86400000).toISOString(),
    })
    const user = JSON.stringify({
      userId: 'usr_student_01',
      email: 'student@university.edu',
      displayName: 'Student Developer',
      locale: 'vi',
      timezone: 'Asia/Ho_Chi_Minh',
    })
    sessionStorage.setItem('flight-web.csrf-session', session)
    sessionStorage.setItem('flight-web.auth-user', user)
    localStorage.setItem('flight-web.csrf-session', session)
    localStorage.setItem('flight-web.auth-user', user)
  })

  console.log('Capturing Hero Assistant Page...')
  await page.goto('http://localhost:5179/assistant', { waitUntil: 'networkidle' })
  await page.waitForTimeout(2000)
  await page.screenshot({ path: path.join(outputDir, 'assistant_hero_page.png') })

  console.log('Capturing Assistant Chat Page...')
  await page.goto('http://localhost:5179/assistant/th_01', { waitUntil: 'networkidle' })
  await page.waitForTimeout(2000)
  await page.screenshot({ path: path.join(outputDir, 'assistant_chat_page.png') })

  console.log('Capturing Search Form Page...')
  await page.goto('http://localhost:5179/search', { waitUntil: 'networkidle' })
  await page.waitForTimeout(1500)
  await page.screenshot({ path: path.join(outputDir, 'flight_search_page.png') })

  console.log('Capturing Bookings Page...')
  await page.goto('http://localhost:5179/bookings', { waitUntil: 'networkidle' })
  await page.waitForTimeout(1500)
  await page.screenshot({ path: path.join(outputDir, 'bookings_page.png') })

  console.log('Capturing Booking Detail & Service Hub Page...')
  await page.goto('http://localhost:5179/bookings/bk_01', { waitUntil: 'networkidle' })
  await page.waitForTimeout(1500)
  await page.screenshot({ path: path.join(outputDir, 'booking_detail_page.png') })

  console.log('Capturing Operations Desk Page...')
  await page.goto('http://localhost:5179/operations', { waitUntil: 'networkidle' })
  await page.waitForTimeout(1500)
  await page.screenshot({ path: path.join(outputDir, 'operations_page.png') })

  console.log('Capturing Travelers Page...')
  await page.goto('http://localhost:5179/travelers', { waitUntil: 'networkidle' })
  await page.waitForTimeout(1500)
  await page.screenshot({ path: path.join(outputDir, 'travelers_page.png') })

  console.log('Capturing Watches Page...')
  await page.goto('http://localhost:5179/watches', { waitUntil: 'networkidle' })
  await page.waitForTimeout(1500)
  await page.screenshot({ path: path.join(outputDir, 'watches_page.png') })

  console.log('Closing browser and stopping dev server...')
  await browser.close()
  vite.kill()

  console.log('All screenshots saved successfully to public/screenshots/')
}

run().catch((err) => {
  console.error(err)
  process.exit(1)
})
