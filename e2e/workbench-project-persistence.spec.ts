import { expect, test } from '@playwright/test'

const project = {
  name: 'E2E Test Project',
  governanceArea: 'Campus resource allocation',
  governanceGoal: 'Test project persistence through the Workbench interface',
}

test('creates a Workbench project and restores it from SQLite after reload', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('campus-pulse-locale-v1', 'zh-CN')
  })
  await page.goto('/campus-pulse/workbench')

  await expect(page.locator('html')).toHaveAttribute('lang', 'zh-CN')
  await expect(page.getByText('内部服务已连接', { exact: true })).toBeVisible()

  const projectPane = page.getByRole('complementary', { name: '模拟项目' })
  await projectPane.getByRole('button', { name: /新建项目$/ }).click()

  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  await dialog.getByRole('button', { name: /自定义项目/ }).click()

  await dialog.getByLabel(/项目名称/).fill(project.name)
  await dialog.getByLabel(/治理领域/).fill(project.governanceArea)
  await dialog.getByLabel(/治理目标/).fill(project.governanceGoal)
  await expect(dialog.getByRole('radio', { name: '仿真压力测试' })).toBeChecked()

  const creationResponsePromise = page.waitForResponse((response) => (
    response.request().method() === 'POST'
    && new URL(response.url()).pathname === '/api/campus-pulse/v1/projects'
  ))
  await dialog.locator('form').locator('button[type="submit"]').click()
  const creationResponse = await creationResponsePromise

  expect(creationResponse.status()).toBe(201)
  const creationBody = await creationResponse.json()
  const projectId = creationBody?.data?.project_id
  expect(projectId).toMatch(/^prj_[0-9a-f]{24}$/)

  const projectRow = projectPane.getByRole('button').filter({
    has: page.getByText(project.name, { exact: true }),
  })
  await expect(projectRow).toBeVisible()
  await expect(projectRow).toHaveAttribute('aria-current', 'page')
  await expect(page).toHaveURL(new RegExp(`[?&]project=${projectId}(?:&|$)`))

  const projectOverview = page.getByRole('region', { name: project.name })
  await expect(projectOverview.getByRole('heading', { name: project.name })).toBeVisible()
  await expect(projectOverview.getByText(project.governanceArea, { exact: true })).toBeVisible()
  await expect(projectOverview.getByText(project.governanceGoal, { exact: true })).toBeVisible()
  await expect(projectOverview.getByText(projectId, { exact: true })).toBeVisible()

  const persistedProjectsResponsePromise = page.waitForResponse((response) => (
    response.request().method() === 'GET'
    && new URL(response.url()).pathname === '/api/campus-pulse/v1/projects'
  ))
  await page.reload()
  const persistedProjectsResponse = await persistedProjectsResponsePromise
  expect(persistedProjectsResponse.status()).toBe(200)
  const persistedProjectsBody = await persistedProjectsResponse.json()
  expect(persistedProjectsBody?.data).toEqual(expect.arrayContaining([
    expect.objectContaining({
      project_id: projectId,
      name: project.name,
      governance_domain: project.governanceArea,
      objective: project.governanceGoal,
      evaluation_mode: 'simulation_stress_test',
    }),
  ]))

  const persistedProjectRow = page
    .getByRole('complementary', { name: '模拟项目' })
    .getByRole('button')
    .filter({ has: page.getByText(project.name, { exact: true }) })
  await expect(persistedProjectRow).toBeVisible()
  await expect(persistedProjectRow).toHaveAttribute('aria-current', 'page')
  await expect(page).toHaveURL(new RegExp(`[?&]project=${projectId}(?:&|$)`))

  const persistedOverview = page.getByRole('region', { name: project.name })
  await expect(persistedOverview.getByRole('heading', { name: project.name })).toBeVisible()
  await expect(persistedOverview.getByText(project.governanceArea, { exact: true })).toBeVisible()
  await expect(persistedOverview.getByText(project.governanceGoal, { exact: true })).toBeVisible()
  await expect(persistedOverview.getByText(projectId, { exact: true })).toBeVisible()
})
