// 仅手动触发；专用 Windows agent 与本地统一调用 tools.run。
pipeline {
    agent { label 'test-zammad-windows' }
    options {
        skipDefaultCheckout(true)
        disableConcurrentBuilds()
        timeout(time: 20, unit: 'MINUTES')
        buildDiscarder(logRotator(daysToKeepStr: '30', numToKeepStr: '50'))
        timestamps()
    }
    parameters {
        choice(name: 'SUITE', choices: ['functional', 'pilot'], description: '当前两项均覆盖已实现的 P0 五条；不运行性能或并发。')
        string(name: 'CONFIG_ROOT', defaultValue: 'F:/Python/test-Zammad/config', description: 'agent 可读的非敏感本机配置目录，含 local.yaml、run.local.yaml、db.local.yaml。')
    }
    environment {
        BASE_PYTHON = 'F:/Python/test-Zammad/.runtime/Python-3.13.13/python.exe'
        PYTHONUTF8 = '1'
        PYTEST_ADDOPTS = ''
        PIP_DISABLE_PIP_VERSION_CHECK = '1'
    }
    stages {
        stage('Checkout') {
            steps { checkout scm }
        }
        stage('Runtime') {
            steps {
                powershell '''
                    $ErrorActionPreference = 'Stop'
                    & $env:BASE_PYTHON -X utf8 -c "import sys; assert sys.version_info[:3] == (3,13,13), 'Python version mismatch'"
                    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
                    if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
                        & $env:BASE_PYTHON -I -X utf8 -m venv .venv --prompt test-Zammad-ci
                        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
                    }
                    & ./.venv/Scripts/python.exe -I -X utf8 -m pip --isolated install --index-url https://pypi.org/simple -r requirements.txt
                    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
                    & ./.venv/Scripts/python.exe -X utf8 -m pip check
                    exit $LASTEXITCODE
                '''
            }
        }
        stage('P0') {
            steps {
                withCredentials([
                    string(credentialsId: 'test-zammad-a1-token', variable: 'ZAMMAD_A1_TOKEN'),
                    string(credentialsId: 'test-zammad-m-token', variable: 'ZAMMAD_M_TOKEN'),
                    string(credentialsId: 'test-zammad-db-password', variable: 'ZAMMAD_DB_PASSWORD')
                ]) {
                    powershell '''
                        $ErrorActionPreference = 'Stop'
                        $m05Config = Join-Path $env:CONFIG_ROOT 'local.yaml'
                        $m05RunConfig = Join-Path $env:CONFIG_ROOT 'run.local.yaml'
                        $m05DbConfig = Join-Path $env:CONFIG_ROOT 'db.local.yaml'
                        $m05Output = 'artifacts/ci-' + $env:BUILD_NUMBER
                        & ./.venv/Scripts/python.exe -X utf8 -m tools.run --suite $env:SUITE --config $m05Config --run-config $m05RunConfig --db-config $m05DbConfig --output $m05Output
                        exit $LASTEXITCODE
                    '''
                }
            }
        }
    }
    post {
        always {
            // 任一发布步骤失败都使构建失败；继续尝试保留其他证据。
            catchError(buildResult: 'FAILURE', stageResult: 'FAILURE') {
                junit testResults: "artifacts/ci-${env.BUILD_NUMBER}/junit.xml", allowEmptyResults: false
            }
            catchError(buildResult: 'FAILURE', stageResult: 'FAILURE') {
                allure includeProperties: false, reportBuildPolicy: 'ALWAYS',
                    results: [[path: "artifacts/ci-${env.BUILD_NUMBER}/allure-results"]]
            }
            catchError(buildResult: 'FAILURE', stageResult: 'FAILURE') {
                archiveArtifacts artifacts: "artifacts/ci-${env.BUILD_NUMBER}/**", excludes: '**/pytest-temp/**', allowEmptyArchive: false
            }
        }
    }
}
