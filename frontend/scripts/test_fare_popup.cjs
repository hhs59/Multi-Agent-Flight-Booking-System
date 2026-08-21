const { chromium } = require('playwright');

async function testFarePopup() {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

  try {
    // 1. Auth via Google
    await page.goto('http://localhost:5173/login', { waitUntil: 'networkidle' });
    await page.click('.google-auth-btn');
    await page.waitForTimeout(300);
    await page.click('.google-account-card');
    await page.waitForTimeout(800);

    // 2. Search flights
    await page.goto('http://localhost:5173/search', { waitUntil: 'networkidle' });
    await page.click('.search-submit', { force: true });
    console.log('Search clicked, waiting for Duffel API offers...');
    
    // Wait for flight cards to render
    await page.waitForSelector('.fare-details-link-btn', { timeout: 20000 });
    console.log('Flight offers rendered! Clicking fare details button...');

    const fareBtn = page.locator('.fare-details-link-btn').first();
    await fareBtn.click({ force: true });
    await page.waitForTimeout(600);
    await page.screenshot({ path: 'frontend/popup_fare_breakdown.png' });
    console.log('✅ Captured Fare Breakdown Pop-up (frontend/popup_fare_breakdown.png)');
  } catch (err) {
    console.error('Error during test:', err);
  } finally {
    await browser.close();
  }
}

testFarePopup();
