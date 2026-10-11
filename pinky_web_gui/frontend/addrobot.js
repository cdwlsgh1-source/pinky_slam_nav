'use strict';
// [로봇 추가] 안내 팝업. 이름, 도메인 ID, IP 를 입력하면 설정 파일에 붙여 넣을 조각과 GUI 밖에서 할 일을 만들어 보여 준다.
// 입력값이 들어간 자리는 입력칸과 같은 색으로 표시한다 (무엇이 입력에 따라 바뀌는지 한눈에 보이게).
// 서버 설정은 여기서 바꾸지 않는다 (복사해서 yaml 에 직접 붙여 넣는다). robots.yaml 의 로봇 목록과 문(RED*) 지점이
// 맞지 않으면 서버가 시작하지 못하므로, 지점 좌표를 확인한 뒤 붙여 넣게 하려는 것이다.
(function () {
  const $ = (id) => document.getElementById(id);
  const NAME_RE = /^[A-Za-z][A-Za-z0-9_]*$/;
  const IP_RE = /^(\d{1,3})(\.\d{1,3}){3}$/;
  const M0 = '\u0001', M1 = '\u0002', M2 = '\u0003';   // 입력값 표시 구간의 시작, 구분, 끝 (복사할 때는 제거한다)

  function values() {
    const name = $('arName').value.trim();
    const domain = $('arDomain').value.trim();
    const ip = $('arIp').value.trim();
    const user = $('arUser').value.trim() || 'pinky';
    const num = (name.match(/(\d+)$/) || [])[1] || 'N';
    let err = '';
    if (!NAME_RE.test(name)) err = '이름은 영문으로 시작하는 영문/숫자/_ 여야 합니다.';
    else if (!/^\d+$/.test(domain) || +domain > 232) err = '도메인 ID 는 0~232 의 정수입니다.';
    else if (!IP_RE.test(ip) || ip.split('.').some((x) => +x > 255)) err = 'IP 형식이 올바르지 않습니다.';
    return { name, domain, ip, user, num, err };
  }

  // k: name | domain | ip | user. 이름에서 뽑은 번호(RED3IN 의 3)는 이름과 같은 색이다.
  const mark = (k, text) => `${M0}${k}${M1}${text}${M2}`;
  const plain = (s) => s.replace(/\u0001\w+\u0002/g, '').replace(/\u0003/g, '');

  function build(v) {
    const n = mark('name', v.name), N = mark('name', v.num), D = mark('domain', v.domain);
    const IP = mark('ip', v.ip), U = mark('user', v.user);
    const env = `pinky${N}_env`;
    const ssh = `ssh: {host: "${IP}", user: "${U}", port: 22}`;
    const src = 'source: ["/opt/ros/jazzy/setup.bash", "/home/pinky/pinky_slam_nav/install/setup.bash"]';
    return {
      robots: `# robots: 목록 끝에 한 줄 추가\n  - ${n}\n\n# robot_settings: 아래에 추가\n# (값은 새 로봇 순찰 노드의 POINTS / WAYPOINTS / HOME_NAME 과 같게)\n  ${n}:\n    goto_allowed: [P1, P2, P3, P4, P5, P6, P7]\n    home: P7\n    goto_wait_sec: 10\n    wait_every_point: true\n    start_route: [RED${N}IN, P3, P6, RED${N}OUT, P7]`,
      zone: `# doors: 아래에 추가\n# (RED${N}IN, RED${N}OUT 은 points.yaml 에 좌표가 있어야 한다)\n  ${n}: {in: RED${N}IN, out: RED${N}OUT}`,
      procs: `# defaults 아래에 추가\n# (값은 ${n} 로봇 터미널의  env | grep -E "RMW|CYCLONE|ROS_"  와 같게)\n  ${env}: &${env}\n    RMW_IMPLEMENTATION: rmw_cyclonedds_cpp\n    CYCLONEDDS_URI: file:///home/pinky/.ros/cyclonedds_robot.xml\n    ROS_AUTOMATIC_DISCOVERY_RANGE: SUBNET\n\n# 같은 단계의 pinky2 항목 바로 뒤에 각각 추가 (순서 = 전체 시작 순서)\n  - id: bringup_${n}\n    group: bringup\n    label: ${n} bringup\n    kind: ssh\n    robot: ${n}\n    health: true\n    domain: ${D}\n    ${ssh}\n    cwd: /home/pinky\n    env: *${env}\n    ${src}\n    command: "ros2 launch pinky_bringup bringup_robot.launch.xml"\n    detect: "ros2 launch pinky_bringup"\n\n  - id: map_${n}\n    group: map\n    label: ${n} map (Nav2)\n    kind: ssh\n    robot: ${n}\n    domain: ${D}\n    ${ssh}\n    cwd: /home/pinky\n    env: *${env}\n    ${src}\n    command: "ros2 launch pinky_navigation bringup_launch.xml map:=my_pinky_map10.yaml"\n    detect: "ros2 launch pinky_navigation"\n\n  - id: patrol_${n}\n    group: "순찰 노드"\n    label: ${n} 순찰 노드\n    kind: ssh\n    robot: ${n}\n    domain: ${D}\n    ${ssh}\n    cwd: /home/pinky/pinky_slam_nav\n    env: *${env}\n    source: ["/opt/ros/jazzy/setup.bash", "./install/setup.bash"]\n    command: "ros2 run my_pinky_package pinky_patrol_node_${n}_v2"\n    detect: "pinky_patrol_node_${n}_v2"`,
      // 각 단계: 제목, 설명(선택), 터미널에 칠 명령/파일 경로(선택)
      steps: [
        { t: 'SSH 키 등록 (관제 PC 터미널)', d: '비밀번호는 터미널에서만 입력합니다. 두 번째 명령은 호스트 키를 한 번 확인하는 것입니다.',
          c: [`ssh-copy-id ${U}@${IP}`, `ssh ${U}@${IP} true`] },
        { t: '관제 PC DDS 설정', d: `~/.ros/cyclonedds_pc.xml 의 unicast peer 에 ${IP} 를 추가합니다.`, c: [`~/.ros/cyclonedds_pc.xml  <-  ${IP}`] },
        { t: '브릿지 설정 파일 만들기', d: '기존 파일이라 직접 수정합니다. pinky2 파일을 복사해 아래 두 값을 바꿉니다.',
          c: [`domain_bridge/config/pinky_bridge_pinky2.yaml  ->  domain_bridge/config/pinky_bridge_${n}.yaml`, `도메인 22 -> ${D}`, `이름 pinky2 -> ${n}`] },
        { t: '브릿지 실행에 추가', d: `domain_bridge 패키지의 bridges.launch.xml 에 ${n} 브릿지를 추가합니다. processes.yaml 의 bridge detect 패턴 "domain_bridge .*pinky_bridge_pinky" 가 새 이름과 맞는지 확인합니다.` },
        { t: 'zone_manager 에 로봇 등록', d: '', c: [`zone_traffic_control/config/zone_params.yaml  의 robot_ids 에 "${n}" 추가`] },
        { t: '순찰 노드 만들기', d: `robot_id 는 "${n}" 입니다. setup.py entry point 에 등록한 뒤 빌드합니다.`,
          c: [`my_pinky_package/my_pinky_package/pinky_patrol_node_${n}_v2.py`, 'colcon build --packages-select my_pinky_package'] },
        { t: `${n} 로봇 설정 (로봇 터미널)`, d: `ROS_DOMAIN_ID 와 CycloneDDS 를 설정하고 pinky_slam_nav 를 설치합니다.`,
          c: [`export ROS_DOMAIN_ID=${D}`, 'export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp', 'export CYCLONEDDS_URI=file:///home/pinky/.ros/cyclonedds_robot.xml'] },
        { t: '문 지점 좌표 추가', d: '순찰 노드의 POINTS 와 같은 값을 넣습니다.', c: [`pinky_web_gui/config/points.yaml  에 RED${N}IN, RED${N}OUT`] },
        { t: 'GUI 서버 재시작', d: '설정이 어긋나면 시작할 때 오류로 나옵니다.' },
      ],
    };
  }

  // 표시용 문자열(마커 포함) -> 색칠된 span 들
  function rich(parent, text) {
    const re = /\u0001(\w+)\u0002([\s\S]*?)\u0003/g;
    let last = 0, m;
    while ((m = re.exec(text))) {
      if (m.index > last) parent.append(text.slice(last, m.index));
      const s = document.createElement('span');
      s.className = 'ar-v ar-v-' + m[1];
      s.textContent = m[2];
      parent.append(s);
      last = re.lastIndex;
    }
    if (last < text.length) parent.append(text.slice(last));
    return parent;
  }

  function block(title, text) {
    const wrap = document.createElement('div');
    wrap.className = 'ar-block';
    const head = document.createElement('div');
    head.className = 'ar-head';
    const t = document.createElement('b'); t.textContent = title;
    const btn = document.createElement('button');
    btn.type = 'button'; btn.textContent = '복사';
    btn.onclick = async () => {
      try { await navigator.clipboard.writeText(plain(text)); btn.textContent = '복사됨'; }
      catch (e) { btn.textContent = '직접 선택해 복사'; }
      setTimeout(() => { btn.textContent = '복사'; }, 1500);
    };
    head.append(t, btn);
    wrap.append(head, rich(document.createElement('pre'), text));
    return wrap;
  }

  function steps(list) {
    const ol = document.createElement('ol');
    ol.className = 'ar-steps';
    for (const s of list) {
      const li = document.createElement('li');
      const t = document.createElement('div'); t.className = 'ar-step-title'; rich(t, s.t);
      li.append(t);
      if (s.d) { const d = document.createElement('div'); d.className = 'ar-step-desc'; rich(d, s.d); li.append(d); }
      for (const line of s.c || []) { const c = document.createElement('code'); rich(c, line); li.append(c); }
      ol.append(li);
    }
    return ol;
  }

  function render() {
    const v = values();
    const out = $('arOut');
    out.replaceChildren();
    $('arErr').textContent = v.err;
    if (v.err) return;
    const b = build(v);
    const h1 = document.createElement('h3'); h1.textContent = 'GUI 설정 (pinky_web_gui/config/)';
    out.append(h1, block('robots.yaml', b.robots), block('zone.yaml', b.zone), block('processes.yaml', b.procs));
    const h2 = document.createElement('h3'); h2.textContent = 'GUI 밖에서 할 일 (순서대로)';
    out.append(h2, steps(b.steps));
  }

  document.addEventListener('DOMContentLoaded', () => {
    const dlg = $('addRobotDlg');
    if (!dlg) return;
    $('addRobotBtn').onclick = () => { render(); dlg.showModal(); };
    $('arClose').onclick = () => dlg.close();
    for (const id of ['arName', 'arDomain', 'arIp', 'arUser']) $(id).addEventListener('input', render);
  });
})();
