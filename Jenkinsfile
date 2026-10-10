// 仅手动触发；专用 Windows agent 与本地统一调用 tools.run。
pipeline {
    agent { label 'test-zammad-windows' }
    options {
        skipDefaultCheckout(true)
        disableConcurrentBuilds()
        timeout(time: 45, unit: 'MINUTES')
        buildDiscarder(logRotator(daysToKeepStr: '30', numToKeepStr: '50'))
        timestamps()
    }
    tools {
        jdk 'test-zammad-jdk21'
    }
    parameters {
        booleanParam(name: 'RUN_TESTS', defaultValue: false, description: '勾选才执行业务测试并准备/清理数据。首次构建保持关闭，只检查工具和配置。')
        choice(name: 'SUITE', choices: ['functional', 'pilot'], description: 'functional：73条功能用例；pilot：P0五条。首次真实构建请选择pilot；不运行性能或并发。')
        string(name: 'CONFIG_ROOT', defaultValue: 'F:/Python/test-Zammad/config', description: '专用 Windows agent 可读的七份非敏感本机配置目录；共享状态仍由 run.local.yaml 指定。')
    }
    environment {
        // 首次非参数化构建也从 params（含声明默认值）显式传给子进程。
        SUITE = "${params.SUITE ?: 'functional'}"
        CONFIG_ROOT = "${params.CONFIG_ROOT ?: 'F:/Python/test-Zammad/config'}"
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
                script {
                    env.ALLURE_HOME = tool name: 'test-zammad-allure',
                        type: 'org.allurereport.jenkins.tools.AllureCommandlineInstallation'
                }
                powershell '''
                    $ErrorActionPreference = 'Stop'
                    if ($env:SUITE -notin @('pilot', 'functional')) { throw 'Invalid test suite' }
                    $configNames = @('local.yaml', 'run.local.yaml', 'db.local.yaml')
                    if ($env:SUITE -eq 'functional') {
                        $configNames += @('creation.local.yaml', 'assignment.local.yaml', 'update_access.local.yaml', 'search.local.yaml')
                    }
                    foreach ($name in $configNames) {
                        if (-not (Test-Path -LiteralPath (Join-Path $env:CONFIG_ROOT $name) -PathType Leaf)) {
                            throw ('Missing configuration: ' + $name)
                        }
                    }
                    & git --version
                    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
                    & java --version
                    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
                    $allureVersion = & (Join-Path $env:ALLURE_HOME 'bin/allure.bat') --version
                    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
                    if (($allureVersion | Out-String).Trim() -ne '2.38.1') { throw 'Allure version mismatch: expected 2.38.1' }
                    & $env:BASE_PYTHON -X utf8 -c "import sys; assert sys.version_info[:3] == (3,13,13), 'Python version mismatch'"
                    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
                    if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
                        & $env:BASE_PYTHON -I -X utf8 -m venv .venv --prompt test-Zammad-ci
                        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
                    }
                    & ./.venv/Scripts/python.exe -I -X utf8 -c "import sys; assert sys.version_info[:3] == (3,13,13), 'CI venv Python version mismatch'"
                    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
                    & ./.venv/Scripts/python.exe -I -X utf8 -m pip --isolated install --timeout 30 --index-url https://pypi.org/simple -r requirements.txt
                    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
                    & ./.venv/Scripts/python.exe -X utf8 -m pip check
                    exit $LASTEXITCODE
                '''
            }
        }
        stage('Tests') {
            when { expression { params.RUN_TESTS } }
            steps {
                script {
                    def credentials = [
                        string(credentialsId: 'test-zammad-a1-token', variable: 'ZAMMAD_A1_TOKEN'),
                        string(credentialsId: 'test-zammad-m-token', variable: 'ZAMMAD_M_TOKEN'),
                        string(credentialsId: 'test-zammad-db-password', variable: 'ZAMMAD_DB_PASSWORD')
                    ]
                    if (params.SUITE == 'functional') {
                        credentials += [
                            string(credentialsId: 'test-zammad-a2-token', variable: 'ZAMMAD_A2_TOKEN'),
                            string(credentialsId: 'test-zammad-a12-token', variable: 'ZAMMAD_A12_TOKEN'),
                            string(credentialsId: 'test-zammad-ar-token', variable: 'ZAMMAD_AR_TOKEN'),
                            string(credentialsId: 'test-zammad-ad-token', variable: 'ZAMMAD_AD_TOKEN'),
                            string(credentialsId: 'test-zammad-c1-token', variable: 'ZAMMAD_C1_TOKEN'),
                            string(credentialsId: 'test-zammad-c2-token', variable: 'ZAMMAD_C2_TOKEN'),
                            string(credentialsId: 'test-zammad-ba1-token', variable: 'ZAMMAD_BA1_TOKEN'),
                            string(credentialsId: 'test-zammad-ba2-token', variable: 'ZAMMAD_BA2_TOKEN'),
                            string(credentialsId: 'test-zammad-bc1-token', variable: 'ZAMMAD_BC1_TOKEN'),
                            string(credentialsId: 'test-zammad-bc2-token', variable: 'ZAMMAD_BC2_TOKEN')
                        ]
                    }
                    withCredentials(credentials) {
                        env.TESTS_STARTED = 'true'
                        // 单个进程持有共享环境锁，直到取证及清理结束。
                        powershell '''
                        $ErrorActionPreference = 'Stop'
                        $runArgs = @(
                            '-X', 'utf8', '-m', 'tools.run', '--suite', $env:SUITE,
                            '--config', (Join-Path $env:CONFIG_ROOT 'local.yaml'),
                            '--run-config', (Join-Path $env:CONFIG_ROOT 'run.local.yaml'),
                            '--db-config', (Join-Path $env:CONFIG_ROOT 'db.local.yaml'),
                            '--creation-config', (Join-Path $env:CONFIG_ROOT 'creation.local.yaml'),
                            '--assignment-config', (Join-Path $env:CONFIG_ROOT 'assignment.local.yaml'),
                            '--update-access-config', (Join-Path $env:CONFIG_ROOT 'update_access.local.yaml'),
                            '--search-config', (Join-Path $env:CONFIG_ROOT 'search.local.yaml'),
                            '--output', ('artifacts/ci-' + $env:BUILD_NUMBER)
                        )
                        & ./.venv/Scripts/python.exe @runArgs
                        exit $LASTEXITCODE
                        '''
                    }
                }
            }
        }
    }
    post {
        always {
            script {
                if (env.TESTS_STARTED == 'true') {
                    // 任一发布步骤失败都使构建失败；继续尝试保留其他证据。
                    catchError(buildResult: 'FAILURE', stageResult: 'FAILURE') {
                        junit testResults: "artifacts/ci-${env.BUILD_NUMBER}/junit.xml", allowEmptyResults: false
                    }
                    catchError(buildResult: 'FAILURE', stageResult: 'FAILURE') {
                        allure includeProperties: false, reportBuildPolicy: 'ALWAYS',
                            allureVersion: '2', commandline: 'test-zammad-allure', jdk: 'test-zammad-jdk21',
                            results: [[path: "artifacts/ci-${env.BUILD_NUMBER}/allure-results"]]
                    }
                    catchError(buildResult: 'FAILURE', stageResult: 'FAILURE') {
                        archiveArtifacts artifacts: "artifacts/ci-${env.BUILD_NUMBER}/**",
                            excludes: '**/pytest-temp/**,**/pytest-cache/**', allowEmptyArchive: false
                    }
                } else {
                    echo 'No business tests executed; environment preparation is not a test pass.'
                }
            }
        }
    }
}
