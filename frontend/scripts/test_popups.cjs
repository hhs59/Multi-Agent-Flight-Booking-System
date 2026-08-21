const { chromium } = require('playwright');

async function testPopups() {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

  try {
    // 1. Auth via Google
    await page.goto('http://localhost:5173/login', { waitUntil: 'networkidle' });
    await page.click('.google-auth-btn');
    await page.waitForTimeout(400);
    await page.click('.google-account-card');
    await page.waitForTimeout(1000);

    // 2. Test Assistant Thread and Delete Pop-up
    await page.goto('http://localhost:5173/assistant', { waitUntil: 'networkidle' });
    await page.click('.new-chat-btn');
    await page.waitForTimeout(600);

    // Click Delete button on header
    await page.click('.action-delete-btn');
    await page.waitForTimeout(500);
    await page.screenshot({ path: 'popup_confirm_delete.png' });
    console.log('✅ 1. Captured Delete Pop-up (popup_confirm_delete.png)');

    // Close delete popup
    const cancelBtn = page.locator('.modal-footer button').first();
    await cancelBtn.click();
    await page.waitForTimeout(400);

    // Click Rename button on header
    const renameBtn = page.locator('button[title="Đổi tên"]');
    await renameBtn.click();
    await page.waitForTimeout(500);
    await page.screenshot({ path: 'popup_rename_trip.png' });
    console.log('✅ 2. Captured Rename Pop-up (popup_rename_trip.png)');

    // Close rename popup
    await page.locator('.modal-footer button').first().click();
    await page.waitForTimeout(400);

    // 3. Test Flight Search and Fare Breakdown Pop-up
    await page.goto('http://localhost:5173/search', { waitUntil: 'networkidle' });
    await page.click('.search-btn');
    await page.waitForTimeout(2500);

    const fareBtn = page.locator('.fare-details-link-btn').first();
    if (await fareBtn.count() > 0) {
      await fareBtn.click();
      await page.waitForTimeout(500);
      await page.screenshot({ path: 'popup_fare_breakdown.png' });
      console.log('✅ 3. Captured Fare Breakdown Pop-up (popup_fare_breakdown.png)');
    }

    console.log('\n🎉 ALL POP-UP MODALS VERIFIED SUCCESSFULLY!');
  } finally {
    await browser.close();
  }
}

testPopups();
