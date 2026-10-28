import { test, expect } from "@playwright/test";
import { register, login, USER } from "./helpers";

test("Карточки брокеров сохраняют общую шапку и навигацию настроек", async ({page}) => {
  await login(page, USER.username, USER.password);
  await page.goto('/settings');
  const heading = await page.locator('.settings-head').innerText();
  await page.locator('.settings-nav').getByRole('link', {name:'Брокеры и торговля'}).click();
  await expect(page.locator('.settings-head')).toHaveText(heading);
  await expect(page.locator('.settings-content > section')).toHaveCount(1);
  await expect(page.locator('.settings-nav [aria-current="page"]')).toHaveText('Брокеры и торговля');
  await expect(page.locator('[data-broker]')).toHaveCount(2);
  await expect(page.getByRole('button',{name:'Добавить своего брокера — скоро'})).toBeDisabled();
  await expect(page.locator('.broker-workspace:visible')).toHaveCount(0);
  await page.locator('[data-broker="sandbox"]').click();
  await expect(page.locator('#broker-workspace-sandbox')).toBeVisible();
  await expect(page.locator('#broker-workspace-production')).toBeHidden();
  await page.screenshot({path:'test-results/broker-settings-desktop.png',fullPage:true});
  await page.locator('[data-broker="sandbox"]').click();
  await expect(page.locator('.broker-workspace:visible')).toHaveCount(0);
  await page.locator('.settings-nav').getByRole('link',{name:'Профиль',exact:true}).click();
  await expect(page).toHaveURL(/settings#profile$/);
  await page.goto('/settings?broker_error=422&environment=sandbox#brokers');
  await expect(page.locator('#broker-workspace-sandbox .broker-connect-panel')).toBeVisible();
  await expect(page.locator('#broker-workspace-sandbox [name="environment"]').first()).toHaveValue('sandbox');
  await page.goto('/settings#brokers');
  await expect(page).toHaveURL(/settings\/brokers$/);
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBeTruthy();
});

test("Отдельные формы токенов, ошибки и отсутствие содержимого без подключения", async ({page}) => {
  await register(page,'broker_'+crypto.randomUUID().slice(0,8),'secret123');
  await page.goto('/settings/brokers');
  await expect(page.locator('.broker-data')).toHaveCount(0);
  await expect(page.locator('script[src="/static/broker-sandbox.js"]')).toHaveCount(0);
  await page.locator('[data-broker="production"]').click();
  const production=page.locator('#broker-workspace-production');
  await expect(production).toContainText('только для чтения');
  await expect(production.locator('[name="environment"]')).toHaveValue('production');
  await page.locator('[data-broker="sandbox"]').click();
  const sandbox=page.locator('#broker-workspace-sandbox');
  await expect(sandbox.locator('[name="environment"]')).toHaveValue('sandbox');
  await expect(production).toBeHidden();
  await page.goto('/settings/brokers?environment=sandbox&broker_error=422');
  await expect(sandbox.getByRole('alert')).toContainText('Не удалось подключить');
  await expect(sandbox.locator('[name="token"]')).toHaveValue('');
  await page.goto('/portfolio/brokers/t-invest');
  await expect(page.locator('#broker-workspace-production')).toBeVisible();
  await page.goto('/settings/brokers?environment=production&broker=connected');
  await expect(page.locator('.broker-workspace:visible')).toHaveCount(0);
});

test("Sandbox раскрывает портфель, настройки подключения открываются отдельно", async ({page})=>{
  let calls=0;
  await page.route('**/v1/brokers/t-invest/sandbox/accounts',route=>{calls++;return route.fulfill({json:{accounts:[],actions:[],error:null}});});
  await login(page,USER.username,USER.password);
  await page.goto('/settings/brokers');
  expect(calls).toBe(0);
  await expect(page.locator('[data-broker="sandbox"]')).toContainText('Подключено');
  await page.locator('[data-broker="sandbox"]').click();
  const workspace=page.locator('#broker-workspace-sandbox');
  await expect(workspace.locator('.broker-connect-panel')).toBeHidden();
  await expect(workspace.locator('#sandbox-empty')).toBeVisible();
  expect(calls).toBe(1);
  await workspace.getByRole('button',{name:'Настройки подключения',exact:true}).click();
  await expect(workspace.locator('.broker-connect-panel')).toBeVisible();
  await expect(workspace.locator('.broker-data')).toBeHidden();
  await expect(workspace.locator('[name="token"]')).toHaveValue('');
  await page.reload();
  await expect(workspace.locator('.broker-connect-panel')).toBeVisible();
});

test("Восстановление сертификата после 502 доступно в настройках нужного контура",async({page})=>{
  await register(page,'cert_'+crypto.randomUUID().slice(0,8),'secret123');
  await page.goto('/settings/brokers?environment=sandbox&broker_error=502');
  const workspace=page.locator('#broker-workspace-sandbox');
  await expect(workspace).toContainText('Проверьте сертификат');
  await expect(workspace.locator('form[action="/broker/t-invest/repair-certificate"] button')).toBeVisible();
});

test("Рабочий контур: счета, баланс, выбор и обновление портфеля",async({page})=>{
  const calls:string[]=[];
  await page.route('**/v1/brokers/t-invest/sandbox/accounts',route=>{calls.push('sandbox');return route.abort();});
  await login(page,'broker-production',USER.password);
  await page.goto('/settings/brokers');
  await page.locator('[data-broker="production"]').click();
  const workspace=page.locator('#broker-workspace-production');
  const first=workspace.locator('.broker-account').filter({has:page.getByRole('heading',{name:'Основной',exact:true})});
  const second=workspace.locator('.broker-account').filter({has:page.getByRole('heading',{name:'Второй',exact:true})});
  await expect(first).toContainText('1500.50 rub');
  await expect(first).toContainText('Выбран для портфеля');
  await expect(second).toContainText('Баланс ещё не загружен');
  await expect(workspace.locator('#sandbox-create')).toHaveCount(0);
  const originalFragment=await workspace.locator(".broker-portfolio-content").evaluate(node=>node.outerHTML);
  await page.route('**/broker/t-invest/select',async route=>{
    const raw=route.request().postData()||'';
    expect(raw).toContain('production');
    const account=raw.includes('production-5678')?'production-5678':'production-1234';calls.push(account);
    await route.fulfill({contentType:'text/html',body:originalFragment.replace('data-selected-account="production-1234"','data-selected-account="'+account+'"')});
  });
  await first.getByRole('button',{name:'Обновить портфель',exact:true}).click();
  await second.getByRole('button',{name:'Выбрать и загрузить портфель',exact:true}).click();
  await expect.poll(()=>calls).toEqual(['production-1234','production-5678']);
  await page.setViewportSize({width:390,height:844});
  await page.goto('/settings/brokers?environment=production#broker-accounts');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBeTruthy();
  await page.screenshot({path:'test-results/broker-production-mobile.png',fullPage:true});
});

test("Оформление карточек сохраняется независимо: цвет, фон и сброс",async({page})=>{
  await register(page,'style_'+crypto.randomUUID().slice(0,8),'secret123');
  await page.goto('/settings/brokers');
  await page.locator('[data-appearance="sandbox"]').click();
  const editor=page.locator('#broker-appearance-sandbox');
  await editor.locator('[name="color"]').fill('#9a60cc');
  await editor.getByRole('button',{name:'Сохранить оформление',exact:true}).click();
  await expect(editor.getByRole('status')).toHaveText('Оформление сохранено.');
  await page.reload();
  await expect(editor.locator('[name="color"]')).toHaveValue('#9a60cc');
  const image=Buffer.from(await page.evaluate(()=>{const canvas=document.createElement('canvas');canvas.width=8;canvas.height=8;canvas.getContext('2d')!.fillRect(0,0,8,8);return canvas.toDataURL('image/png').split(',')[1];}),'base64');
  await editor.locator('[name="image"]').setInputFiles({name:'background.png',mimeType:'image/png',buffer:image});
  await editor.getByRole('button',{name:'Сохранить оформление',exact:true}).click();
  const background=page.locator('[data-broker="sandbox"] img');
  await expect(background).toBeVisible();
  const response=await page.request.get((await background.getAttribute('src'))!);
  expect(response.status()).toBe(200);expect(response.headers()['content-type']).toBe('image/webp');
  await page.locator('[data-appearance="production"]').click();
  await expect(page.locator('#broker-appearance-production [name="color"]')).toHaveValue('#6897ff');
  await page.locator('[data-appearance="sandbox"]').click();
  await editor.getByRole('button',{name:'Стандартный вид',exact:true}).click();
  await expect(editor.locator('[name="color"]')).toHaveValue('#6897ff');
  await expect(background).toHaveCount(0);
});


test("Оба контура подключены независимо: переключение и отключение Sandbox",async({page})=>{
  await page.route('**/v1/brokers/t-invest/sandbox/accounts',route=>route.fulfill({json:{accounts:[],actions:[],error:null}}));
  await login(page,'broker-production',USER.password);
  await page.goto('/settings/brokers');
  await expect(page.locator('[data-broker="production"]')).toContainText('Подключено');
  await expect(page.locator('[data-broker="sandbox"]')).toContainText('Подключено');
  await page.locator('[data-broker="sandbox"]').click();
  const sandbox=page.locator('#broker-workspace-sandbox');
  await sandbox.getByRole('button',{name:'Настройки подключения',exact:true}).click();
  page.once('dialog',dialog=>dialog.accept());
  await sandbox.getByRole('button',{name:'Отключить',exact:true}).click();
  await expect(page.locator('[data-broker="sandbox"]')).toContainText('Не подключено');
  await expect(page.locator('[data-broker="production"]')).toContainText('Подключено');
  await page.locator('[data-broker="production"]').click();
  await expect(page.locator('#broker-portfolio-production')).toContainText('2500.50 rub');
});
