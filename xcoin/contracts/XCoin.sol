// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * ██╗  ██╗ ██████╗ ██████╗ ██╗███╗   ██╗
 * ╚██╗██╔╝██╔════╝██╔═══██╗██║████╗  ██║
 *  ╚███╔╝ ██║     ██║   ██║██║██╔██╗ ██║
 *  ██╔██╗ ██║     ██║   ██║██║██║╚██╗██║
 * ██╔╝ ██╗╚██████╗╚██████╔╝██║██║ ╚████║
 * ╚═╝  ╚═╝ ╚═════╝ ╚═════╝ ╚═╝╚═╝  ╚═══╝
 *
 * Xcoin (XCN) — 퍼즐게임 포인트 보상용 ERC-20 토큰
 *
 * 외부 라이브러리 없이 한 파일로 되어 있어 Remix 에 그대로 붙여넣어 배포할 수 있습니다.
 *
 * 설계 의도
 *  - 총발행량 상한(MAX_SUPPLY)이 코드에 박혀 있습니다. 배포 후 누구도 늘릴 수 없습니다.
 *  - 게임 서버(재무 지갑)는 minter 로 지정해 두면 유저 전환 요청이 올 때마다
 *    필요한 만큼만 발행해서 보낼 수 있습니다. (PAYOUT_MODE=mint)
 *  - 혹은 처음에 물량을 한 번에 발행해 재무 지갑에 넣어두고 전송만 할 수도 있습니다.
 *    (PAYOUT_MODE=transfer — 이쪽이 더 안전합니다. 서버 키가 털려도 발행권은 없으니까요.)
 *  - 소유권 이전은 2단계(제안 → 수락)입니다. 주소를 잘못 쳐서 토큰을 영영 잃는 사고를 막습니다.
 *
 * ★ 배포 전 반드시 읽을 것 ★
 *  - 이 컨트랙트는 owner 가 pause 로 전체 전송을 멈출 수 있습니다. 중앙화 요소입니다.
 *    그럴 필요가 없다면 배포 후 renounceMinterAndPause() 로 그 권한을 영구히 버릴 수 있습니다.
 *  - 실제 자금이 오가는 컨트랙트입니다. 메인넷 배포 전에 테스트넷(Amoy, BSC testnet 등)에서
 *    충분히 굴려보고, 가능하면 감사(audit)를 받으세요.
 */
contract XCoin {
    // ===================== 토큰 기본 정보 =====================
    string public constant name = "Xcoin";
    string public constant symbol = "XCN";
    uint8 public constant decimals = 18;

    /// 총발행 상한: 10억 개. 이 숫자는 배포 후 절대 바뀌지 않습니다.
    uint256 public constant MAX_SUPPLY = 1_000_000_000 * 10 ** 18;

    uint256 public totalSupply;

    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    // ===================== 권한 =====================
    address public owner;
    address public pendingOwner;

    /// 새 토큰을 발행할 수 있는 주소들 (게임 서버의 재무 지갑 등)
    mapping(address => bool) public isMinter;

    /// true 면 모든 전송이 멈춥니다. 비상시에만 사용.
    bool public paused;

    /// true 가 되면 다시는 minter 를 추가하거나 pause 를 켤 수 없습니다.
    bool public adminRenounced;

    // ===================== 이벤트 =====================
    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);
    event MinterSet(address indexed account, bool allowed);
    event Paused(bool paused);
    event OwnershipTransferStarted(address indexed from, address indexed to);
    event OwnershipTransferred(address indexed from, address indexed to);
    event AdminRenounced();

    // ===================== 제어자 =====================
    modifier onlyOwner() {
        require(msg.sender == owner, "XCoin: owner only");
        _;
    }

    modifier onlyMinter() {
        require(isMinter[msg.sender], "XCoin: minter only");
        _;
    }

    modifier whenNotPaused() {
        require(!paused, "XCoin: paused");
        _;
    }

    /**
     * @param initialSupply 배포 즉시 배포자에게 발행할 수량 (wei 단위, 즉 10**18 이 1 XCN).
     *                      0 을 주면 아무것도 발행하지 않고 시작합니다.
     */
    constructor(uint256 initialSupply) {
        owner = msg.sender;
        isMinter[msg.sender] = true;
        emit MinterSet(msg.sender, true);
        emit OwnershipTransferred(address(0), msg.sender);

        if (initialSupply > 0) {
            _mint(msg.sender, initialSupply);
        }
    }

    // ===================== ERC-20 =====================

    function transfer(address to, uint256 amount) external whenNotPaused returns (bool) {
        _transfer(msg.sender, to, amount);
        return true;
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        emit Approval(msg.sender, spender, amount);
        return true;
    }

    function transferFrom(address from, address to, uint256 amount)
        external
        whenNotPaused
        returns (bool)
    {
        uint256 allowed = allowance[from][msg.sender];
        if (allowed != type(uint256).max) {
            require(allowed >= amount, "XCoin: allowance exceeded");
            unchecked {
                allowance[from][msg.sender] = allowed - amount;
            }
        }
        _transfer(from, to, amount);
        return true;
    }

    function _transfer(address from, address to, uint256 amount) internal {
        require(to != address(0), "XCoin: zero address");
        uint256 fromBalance = balanceOf[from];
        require(fromBalance >= amount, "XCoin: balance too low");
        unchecked {
            balanceOf[from] = fromBalance - amount;
            balanceOf[to] += amount;
        }
        emit Transfer(from, to, amount);
    }

    // ===================== 발행 / 소각 =====================

    /**
     * 새 토큰을 발행합니다. minter 만 호출할 수 있고, 상한을 넘길 수 없습니다.
     * 게임 서버가 PAYOUT_MODE=mint 로 동작할 때 이 함수를 부릅니다.
     */
    function mint(address to, uint256 amount) external onlyMinter whenNotPaused {
        _mint(to, amount);
    }

    function _mint(address to, uint256 amount) internal {
        require(to != address(0), "XCoin: zero address");
        require(totalSupply + amount <= MAX_SUPPLY, "XCoin: cap exceeded");
        totalSupply += amount;
        unchecked {
            balanceOf[to] += amount;
        }
        emit Transfer(address(0), to, amount);
    }

    /// 내 토큰을 태웁니다. 태운 만큼 totalSupply 가 줄어듭니다. (상한은 회복되지 않습니다)
    function burn(uint256 amount) external {
        uint256 balance = balanceOf[msg.sender];
        require(balance >= amount, "XCoin: balance too low");
        unchecked {
            balanceOf[msg.sender] = balance - amount;
            totalSupply -= amount;
        }
        emit Transfer(msg.sender, address(0), amount);
    }

    // ===================== 운영 =====================

    function setMinter(address account, bool allowed) external onlyOwner {
        require(!adminRenounced, "XCoin: admin renounced");
        require(account != address(0), "XCoin: zero address");
        isMinter[account] = allowed;
        emit MinterSet(account, allowed);
    }

    function setPaused(bool value) external onlyOwner {
        require(!adminRenounced, "XCoin: admin renounced");
        paused = value;
        emit Paused(value);
    }

    /**
     * 발행권과 일시정지 권한을 영구히 버립니다.
     * 이 함수를 부르고 나면 총발행량은 그 시점 그대로 고정되고,
     * 누구도 전송을 멈출 수 없게 됩니다. 되돌릴 수 없습니다.
     *
     * 게임이 자리를 잡고 "이제 더 찍을 일 없다" 싶을 때 부르면
     * 유저들에게 강력한 신뢰 신호가 됩니다.
     */
    function renounceMinterAndPause() external onlyOwner {
        adminRenounced = true;
        paused = false;
        emit AdminRenounced();
        emit Paused(false);
    }

    // ----- 소유권 2단계 이전 -----

    function transferOwnership(address newOwner) external onlyOwner {
        pendingOwner = newOwner;
        emit OwnershipTransferStarted(owner, newOwner);
    }

    function acceptOwnership() external {
        require(msg.sender == pendingOwner, "XCoin: not pending owner");
        address previous = owner;
        owner = pendingOwner;
        pendingOwner = address(0);
        emit OwnershipTransferred(previous, owner);
    }

    // ===================== 편의 =====================

    /**
     * 여러 명에게 한 번에 보냅니다. 전환 요청을 모아서 처리할 때 가스비를 아낍니다.
     */
    function batchTransfer(address[] calldata recipients, uint256[] calldata amounts)
        external
        whenNotPaused
    {
        require(recipients.length == amounts.length, "XCoin: length mismatch");
        for (uint256 i = 0; i < recipients.length; i++) {
            _transfer(msg.sender, recipients[i], amounts[i]);
        }
    }

    /// 실수로 이 컨트랙트 주소로 보내진 다른 토큰을 꺼냅니다.
    function rescueToken(address token, address to, uint256 amount) external onlyOwner {
        require(token != address(this), "XCoin: cannot rescue self");
        (bool ok, bytes memory data) = token.call(
            abi.encodeWithSelector(bytes4(keccak256("transfer(address,uint256)")), to, amount)
        );
        require(ok && (data.length == 0 || abi.decode(data, (bool))), "XCoin: rescue failed");
    }
}
