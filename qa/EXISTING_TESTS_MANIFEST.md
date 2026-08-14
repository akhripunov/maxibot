# Existing test manifest

This manifest records the pre-existing project tests before QA-only additions. Their SHA-256 values are a preservation record. These files remain in place and run in the regression baseline.

| File | SHA-256 |
|---|---|
| `tests/regression/test_existing_functionality.py` | `19528aae40ead486a18a304e9999b6fe4f30362a5d4bb36e3118f56e246e9378` |
| `tests/test_auto_approve.py` | `ef484fecf73c6a457c7ac3e0c4f5b54889bc2e4c3dfe51d3f41245ea7a00f5b4` |
| `tests/test_core.py` | `f4217872339a956ac8111a37887edb8067af87971df065e978844ab83a01f8ba` |
| `tests/test_demo_flows.py` | `3d36e5950f32ed6cfe8689f896651e0dfaba229274c785640cbbdaf24c960ed1` |
| `tests/test_landing_ui_contract.py` | `8c002a6ddae7d075404ee42fda354cef9f05b04ce03821f823213342e9388a0e` |
| `tests/test_max_auth_state.py` | `31d169b6d06477ea02e425805049784abcf167b9136f3d8a650260388c927e14` |
| `tests/test_max_binary_codec.py` | `f1299bd1b7de6059650bc72f6bb128dbe1868a878896c86641437bac81593d63` |
| `tests/test_max_cdp_runtime_probe.py` | `337a24a219d5f0fb06f10461ae1fa8e228c1a88daf63bf64b8af39f1e972c4f8` |
| `tests/test_max_channel_sync.py` | `0f17f01b5ac373bb7f1ab0c75272481fd51f022bdcc9a4fb90601f6d129e08df` |
| `tests/test_max_payload.py` | `9e23231b63a4e47917c749819a4b6e252c8229f4fdacb47b453d87a1e450a00c` |
| `tests/test_max_web_session.py` | `74637bf2ebc50250899a29e75dca49c9b4335a7b7e4b118594688fcca4ef5f84` |
| `tests/test_metrica_sender.py` | `c9d9c74beda0b620c176aaae634c8e642ebef7cdc64b9775411afab730710903` |
| `tests/test_public_landing_url.py` | `be9bfc3d08d3c4c874aeb7d193831bf5ec55e2a33815d37a2158efa73b821e24` |
| `tests/test_real_max_client.py` | `fbac38a9f5befae952010155209c66370ff2fd73e7a4ebc1e091afed5331b59e` |
| `tests/test_runtime_entrypoints.py` | `c907f4070c4c2973877891614c1e93a1647ba6a2ee6bbd3dbc4030e0fd521b94` |
| `tests/test_scenarios.py` | `fe5ecb8d05d6fe8641ed8c2763abf5c74a6b0b22e96db1568c22f83d78aaea7c` |
| `tests/test_traffic_attribution.py` | `02d3f6d33e71f8ce8f20bc7207e2cc08b61853454b4bd182f1731eaf72bb75e5` |
| `tests/test_traffic_deep_link.py` | `e34e6e5aa4c58ecb6878b62ca1a025bc50d1c7642bbdb919acbe5c059ba608b4` |
| `tests/test_traffic_simulator.py` | `6189845994296e8effaf8ad20fe49dd388cec34284d95ae54c4188a5cf46316a` |
| `tests/test_traffic_v2.py` | `4dac0014dab247a796e5fdb5b9e3b2f86fd8cbed577329dcb37e604b62418835` |
| `tests/test_update_normalization.py` | `f8bc5504a8d1b130cb2e625b0087d0abb448a5e0e812fa6e11b8656aec033064` |
| `tests/test_update_payload.py` | `b91c2c21f8818c7967566c009eeffb7b272079160a5081e1b910106bc67108ac` |
| `tests/test_yandex_metrica.py` | `e937202f3652a673fba2a7f477a7c85ec83897fb08c1908c3559fca79959b446` |
| `tests/test_yandex_oauth.py` | `9f901630ca213cce1c4acb3f135ecce2222f8e48448d767d246417d77723081e` |

QA-only additions are separate: `tests/conftest.py`, `tests/unit/`, `tests/integration/`, `tests/e2e/`, `tests/smoke/`, and `tests/regression/test_qa_regression_contract.py`.
