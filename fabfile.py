# vim: ai ts=4 sts=4 et sw=4 ft=python fdm=indent et foldlevel=0

# fabric task file for deploying a new CentOS 7 AWS EC2 instance
#
# usage:
#       fab it (deploys and provision a new EC2 instance)
#       fab destroy (destroys the current EC2 instance)
#       fab up (boots an existing or a new EC2 instance)
#       fab down (shutdowns an existing EC2 instance)
#
# state is kept locally in a file called state.json, it contains metadata
# related to the existing EC2 instance.
#
# the following environment variables must be set:
# AWS_AMI
# AWS_INSTANCE_TYPE
# AWS_ACCESS_KEY_ID
# AWS_ACCESS_KEY_FILENAME
# AWS_ACCESS_KEY_PAIR
# AWS_ACCESS_REGION
# AWS_SECRET_ACCESS_KEY


from fabric.api import sudo, local, warn_only, task, env, execute
from subprocess import check_output
from time import sleep
from fabric.colors import green as _green, yellow as _yellow, red as _red
import os
import json
import socket
from textwrap import dedent
from urlparse import urljoin

try:
    import boto.ec2
    from boto.ec2.blockdevicemapping import BlockDeviceType
    from boto.ec2.blockdevicemapping import BlockDeviceMapping
    from boto.ec2.blockdevicemapping import EBSBlockDeviceType
except:
    #  we assume we are running in a virtualenv
    install_python_module('boto')
    import boto.ec2


def connect_to_ec2():
    """ returns a connection object to AWS EC2  """
    conn = boto.ec2.connect_to_region(env.ec2_region,
                                      aws_access_key_id=env.ec2_key,
                                      aws_secret_access_key=env.ec2_secret)
    return conn


def is_there_state():
    """ checks is there is valid state available on disk """
    if os.path.isfile('data.json'):
        return True
    else:
        return False


def is_ssh_available(host, port=22):
    """ checks if ssh port is open """
    s = socket.socket()
    try:
        s.connect((host, port))
        return True
    except socket.error, e:
        return False


def wait_for_ssh(host, port=22, timeout=600):
    """ probes the ssh port and waits until it is available """
    yellow('waiting for ssh...')
    for iteration in xrange(1, timeout):
        if is_ssh_available(host, port):
            green('ssh is now available.')
            return True
        else:
            yellow('waiting for ssh...')
        sleep(1)


def green(msg):
    """ prints it back in green """
    print(_green(msg))


def yellow(msg):
    """ prints it back in yellow """
    print(_yellow(msg))


def red(msg):
    """ prints it back in red """
    print(_red(msg))


def create_server():
    """
    Creates EC2 Instance and saves it state in a local json file
    """
    # looks for an existing 'data.json' file, so that we don't start
    # additional ec2 instances when we don't need them.
    #
    if is_there_state():
        return True
    else:
        conn = connect_to_ec2()

        print(_green("Started..."))
        print(_yellow("...Creating EC2 instance..."))

        # we need a larger boot device to store our cached images
        dev_sda1 = EBSBlockDeviceType()
        dev_sda1.size = 120
        bdm = BlockDeviceMapping()
        bdm['/dev/sda1'] = dev_sda1

        # get an ec2 ami image object with our choosen ami
        image = conn.get_all_images(env.ec2_ami)[0]
        # start a new instance
        reservation = image.run(1, 1,
                                key_name=env.ec2_key_pair,
                                security_groups=env.ec2_security,
                                block_device_map = bdm,
                                instance_type=env.ec2_instancetype)

        # and get our instance_id
        instance = reservation.instances[0]
        # add a tag to our instance
        conn.create_tags([instance.id], {"Name": env.ec2_instance_name})
        #  and loop and wait until ssh is available
        while instance.state == u'pending':
            yellow("Instance state: %s" % instance.state)
            sleep(10)
            instance.update()
        wait_for_ssh(instance.public_dns_name)

        green("Instance state: %s" % instance.state)
        green("Public dns: %s" % instance.public_dns_name)
        # finally save the details or our new instance into the local state file
        save_state_locally(instance.id)


def save_state_locally(instance_id):
    """ queries EC2 for details about a particular instance_id and
        stores those details locally
    """
    data = get_ec2_info(instance_id)
    with open('data.json', 'w') as f:
        json.dump(data, f)


def load_state_from_disk():
    """ saves state in a local 'data.json' file so that it can be
        reused between fabric runs.
    """
    if is_there_state():
        import json
        with open('data.json', 'r') as f:
            data = json.load(f)
        return data
    else:
        return False


@task
def print_ec2_info():
    """ outputs information about our EC2 instance """
    _state = load_state_from_disk()
    if _state:
        data = get_ec2_info(_state['id'])
        green("Instance state: %s" % data['state'])
        green("Public dns: %s" % data['public_dns_name'])
        green("Ip address: %s" % data['ip_address'])
        green("volume: %s" % data['volume'])
        green("user: %s" % env.user)
        green("ssh -i %s %s@%s" % (env.key_filename,
                                        env.user,
                                        data['ip_address']))


def get_ec2_info(instance_id):
    """ queries EC2 for details about a particular instance_id
    """
    conn = connect_to_ec2()
    instance = conn.get_only_instances(
        filters={'instance_id': instance_id}
        )[0]


    data = {}
    data['public_dns_name'] = instance.public_dns_name
    data['id'] = instance.id
    data['ip_address'] = instance.ip_address
    data['architecture'] = instance.architecture
    data['state'] = instance.state
    try:
        volume = conn.get_all_volumes(
            filters={'attachment.instance-id': instance.id})[0].id
        data['volume'] = volume
    except:
        data['volume'] = ''
    return data


@task
def rsync():
    """ syncs the src code to the remote box """
    from fabric.context_managers import lcd
    green('syncing code to remote box...')
    data = load_state_from_disk()
    if 'SOURCE_PATH' in os.environ:
        with lcd(os.environ['SOURCE_PATH']):
            local("rsync  -a "
                "--info=progress2 "
                "--exclude .git "
                "--exclude .tox "
                "--exclude .vagrant "
                "--exclude venv "
                ". "
                "-e 'ssh -C -i " + env.ec2_key_filename + "' "
                "%s@%s:" % (env.user, data['ip_address']))
    else:
        print('please export SOURCE_PATH before running rsync')
        exit(1)


@task
def status():
    print_ec2_info()


@task
def up():
    """ boots an existing ec2_instance, or creates a new one if needed """
    # if we don't have a state file, then its likely we need to create a new
    # ec2 instance.
    if is_there_state() is False:
        create_server()
    else:
        conn = connect_to_ec2()
        # there is a data.json file, which contains our ec2 instance_id
        data = load_state_from_disk()
        # boot the ec2 instance
        instance = conn.start_instances(instance_ids=[data['id']])[0]
        while instance.state != "running":
            print(_yellow("Instance state: %s" % instance.state))
            sleep(10)
            instance.update()
        # the ip_address has changed so we need to get the latest data from ec2
        data = get_ec2_info(data['id'])
        # and make sure we don't return until the instance is fully up
        wait_for_ssh(data['ip_address'])
        # lets update our local state file with the new ip_address
        save_state_locally(instance.id)
        env.hosts = data['ip_address']
        print_ec2_info()


@task
def down():
    """ shutdown of an existing EC2 instance """
    conn = connect_to_ec2()
    # checks for a valid state file, containing the details our ec2 instance
    if is_there_state() is False:
        # we can't shutdown the instance, if we don't know which one it is
        return False
    else:
        # get the instance_id from the state file, and stop the instance
        data = load_state_from_disk()
        instance = conn.stop_instances(instance_ids=[data['id']])[0]
        while instance.state != "stopped":
            print(_yellow("Instance state: %s" % instance.state))
            sleep(10)
            instance.update()


@task
def halt():
    down()


@task
def destroy():
    """ terminates the instance """
    if is_there_state() is False:
        return True
    else:
        conn = connect_to_ec2()
        _state = load_state_from_disk()
        data = get_ec2_info(_state['id'])
        instance = conn.terminate_instances(instance_ids=[data['id']])[0]
        yellow('destroying instance ...')
        while instance.state != "terminated":
            print(_yellow("Instance state: %s" % instance.state))
            sleep(10)
            instance.update()
        volume = data['volume']
        if volume:
            yellow('destroying EBS volume ...')
            conn.delete_volume(volume)
        os.unlink('data.json')


@task
def terminate():
    destroy()


def install_python_module(name):
    """ instals a python module using pip """
    local('pip install %s' % name)


def disable_selinux():
    """ disables selinux """
    from fabric.contrib.files import sed, contains

    if contains(filename='/etc/selinux/config',
                text='SELINUX=enforcing'):
        sed('/etc/selinux/config',
            'SELINUX=enforcing', 'SELINUX=disabled', use_sudo=True)

    if contains(filename='/etc/selinux/config',
                text='SELINUXTYPE=enforcing'):
        sed('/etc/selinux/config',
            'SELINUXTYPE=enforcing', 'SELINUX=targeted', use_sudo=True)


def yum_install(**kwargs):
    """
        installs a yum package
    """
    for pkg in list(kwargs['packages']):
        if is_package_installed(pkg) is False:
            if 'repo' in kwargs:
                green("installing %s from repo %s ..." % (pkg, repo))
                sudo("yum install -y --quiet --enablerepo=%s %s" % (repo, pkg))
            else:
                green("installing %s ..." % pkg)
                sudo("yum install -y --quiet %s" % pkg)


def yum_install_from_url(pkg_name, url):
    """ installs a pkg from a url
        p pkg_name: the name of the package to install
        p url: the full URL for the rpm package
    """
    from fabric.api import settings
    from fabric.context_managers import hide

    if is_package_installed(pkg_name) is False:
        green("installing %s from %s" % (pkg_name, url))
        with settings(hide('warnings', 'running', 'stdout', 'stderr'),
                    warn_only=True, capture=True):

            result = sudo("yum install --quiet -y %s" % url)
            if result.return_code == 0:
                return True
            elif result.return_code == 1:
                return False
            else: #print error to user
                print result
                raise SystemExit()


def systemd(service, start=True, enabled=True, unmask=False):
    """ manipulates systemd services """
    from fabric.api import settings
    from fabric.context_managers import hide

    with settings(hide('warnings', 'running', 'stdout', 'stderr'),
                warn_only=True, capture=True):

        if start:
            sudo('systemctl start %s' % service)
        else:
            sudo('systemctl stop %s' % service)

        if enabled:
            sudo('systemctl enable %s' % service)
        else:
            sudo('systemctl disable %s' % service)

        if unmask:
            sudo('systemctl unmask %s' % service)


def sleep_for_one_minute():
    local(sleep(60))


def reboot():
    sudo('shutdown -r now')


def is_package_installed(pkg):
    """ checks if a particular rpm package is installed """
    from fabric.api import settings
    from fabric.context_managers import hide

    with settings(hide('warnings', 'running', 'stdout', 'stderr'),
                  warn_only=True, capture=True):

        result = sudo("rpm -q %s" % pkg)
        if result.return_code == 0:
            return True
        elif result.return_code == 1:
            return False
        else: #print error to user
            print result
            raise SystemExit()


def install_os_updates():
    """ installs OS updates """
    sudo("yum -y --quiet update")


def install_development_packages():
    """ Update the kernel and install some development tools necessary for
     building the ZFS kernel module. """
    yum_install(packages = ["kernel-devel", "kernel", "kernel-headers",
                "dkms", "gcc", "git", "make", "psutils-perl", "lsof", "rsync"])


def add_epel_yum_repository():
    """ Install a repository that provides epel packages/updates """
    yum_install(packages=["epel-release"])


def arch():
    """ returns the current cpu archictecture """
    from fabric.api import settings
    from fabric.context_managers import hide

    with settings(hide('warnings', 'running', 'stdout', 'stderr'),
                  warn_only=True, capture=True):
        result = sudo('rpm -E %dist').strip()
    return result


def create_docker_group():
    """ creates the docker group """
    from fabric.contrib.files import contains

    if not contains('/etc/group', 'docker', use_sudo=True):
        sudo("groupadd docker")


def enable_firewalld_service():
    """ install and enables the firewalld service """
    yum_install(packages=['firewalld'])
    systemd(service='firewalld', unmask=True)


def add_firewall_service(service, permanent=True):
    """ adds a firewall rule """
    yum_install(packages=['firewalld'])
    from fabric.api import settings
    from fabric.context_managers import hide

    with settings(hide('warnings', 'running', 'stdout', 'stderr'),
                warn_only=True, capture=True):
        p = ''
        if permanent:
            p = '--permanent'
        sudo('firewall-cmd --add-service %s %s' % (service, p))


def add_firewall_port(port, permanent=True):
    """ adds a firewall rule """
    yum_install(packages=['firewalld'])
    from fabric.api import settings
    from fabric.context_managers import hide

    with settings(hide('warnings', 'running', 'stdout', 'stderr'),
                warn_only=True, capture=True):
        p = ''
        if permanent:
            p = '--permanent'
        sudo('firewall-cmd --add-port %s %s' % (port, p))

@task
def ssh(*cli):
    from itertools import chain
    """ opens a ssh shell to the host """
    data = load_state_from_disk()
    local('ssh -t -i %s %s@%s %s' % (env['ec2_key_filename'],
                               env['user'], data['ip_address'],
                               "".join(chain.from_iterable(cli))))


def install_docker():
    """ installs docker """
    yum_install(packages=['docker', 'docker-registry'])
    systemd('docker.service')


def pull_docker_image(image):
    """ pulls a docker image """
    from fabric.api import settings
    from fabric.context_managers import hide

    with settings(hide('warnings', 'running', 'stdout', 'stderr'),
                warn_only=True, capture=True):

        result = sudo("docker pull %s" % image)
        if result.return_code == 0:
            return True
        elif result.return_code == 1:
            return False
        else: #print error to user
            print result
            raise SystemExit()


def cache_docker_images():
    """ pulls some docker images locally """
    green('refreshing docker images...')
    for image in ["busybox",
                  "clusterhq/mongodb",
                  "redis",
                  "clusterhq/flask",
                  "python:2.7-slim"]:
        pull_docker_image(image)


def check_for_missing_environment_variables():
    """ double checks that the minimum environment variables have been setup """
    env_var_missing = []
    for env_var in ['AWS_KEY_PAIR',
                    'AWS_KEY_FILENAME',
                    'AWS_SECRET_ACCESS_KEY',
                    'AWS_ACCESS_KEY_ID']:
        if not env_var in os.environ:
            env_var_missing.append(env_var)

    if env_var_missing:
        print('the following environment variables must be set:')
        for env_var in env_var_missing:
            print(env_var)
        return True


def does_container_exist(container):
    from fabric.api import settings
    with settings(warn_only=True):
        result = sudo('docker inspect %s' % container)
        print('*********************************************')
        red(result.return_code)
    if result.return_code is 0:
        return True
    else:
        return False


def get_image_id(image):
        result = sudo("docker images | grep %s | awk '{print $3}'" % image)
        return result

def get_container_id(container):
        result = sudo("docker ps -a | grep %s | awk '{print $1}'" % container)
        return result

def does_image_exist(image):
    from fabric.api import settings
    with settings(warn_only=True):
        result = sudo('docker images')
        if image in result:
            return True
        else:
            return False

def remove_image(image):
    sudo('docker rmi -f %s' % get_image_id(image))

def remove_container(container):
    sudo('docker rm -f %s' % get_container_id(container))

def git_clone_grafana():
    sudo('rm -rf graphite_docker')
    sudo('git clone https://github.com/SamSaffron/graphite_docker.git')

def build_metrics_platform_image():
    sudo('cd graphite_docker && docker build -t metrics_platform_img .')

@task
def deploy_metrics_platform():
    if does_container_exist('metrics_platform'):
        yellow('removing old data-platform container ...')
        remove_container('metrics_platform')

    if does_image_exist('metrics_platform_img'):
        yellow('removing old metrics_platform image ...')
        remove_image('metrics_platform_img')

    git_clone_grafana()
    build_metrics_platform_image()

    sudo('test -e `pwd`/data || mkdir `pwd`/data')
    sudo('chmod 777 `pwd`/data')
    sudo('docker run -d --name metrics_platform -it -v `pwd`/data:/data '
         '-p 14000:80 -p 14001:3000 -p 2003:2003 -p 8125:8125/udp'
         ' metrics_platform_img')

    add_firewall_port('14000/tcp')
    add_firewall_port('14001/tcp')
    add_firewall_port('8125/udp')
    add_firewall_port('2003/tcp')


@task
def it():
    """ runs the full stack """
    execute(up)
    # ec2 hosts get their ip addresses using dhcp, we need to know the new
    # ip address of our box before we continue our provisioning tasks.
    # we load the state from disk, and store the ip in ec2_host#
    ec2_host="%s@%s" %(env.user, load_state_from_disk()['ip_address'])
    execute(disable_selinux, hosts=ec2_host)
    execute(down, hosts=ec2_host)
    execute(up, hosts=ec2_host)
    ec2_host="%s@%s" %(env.user, load_state_from_disk()['ip_address'])
    execute(install_os_updates, hosts=ec2_host)
    execute(add_epel_yum_repository, hosts=ec2_host)
    execute(install_development_packages, hosts=ec2_host)
    execute(enable_firewalld_service, hosts=ec2_host)
    execute(install_docker, hosts=ec2_host)
    execute(create_docker_group, hosts=ec2_host)
    execute(deploy_metrics_platform, hosts=ec2_host)


def main():
    """ loads our environment variables into the env dict.
        checks for existing local state and loads the env dict with the state
        from the last fabric run.

        We store the state in a local file as we need to keep track of the
        ec2 instance id and ip_address so that we can run provision multiple
        times
    """

    if check_for_missing_environment_variables():
        exit(1)

    env.ec2_ami = os.getenv('AWS_AMI', 'ami-c7d092f7')
    env.ec2_instance_name = 'data-platform'
    env.ec2_instancetype = os.getenv('AWS_INSTANCE_TYPE', 't2.micro')
    env.ec2_key = os.environ['AWS_ACCESS_KEY_ID']
    env.ec2_key_filename = os.environ['AWS_KEY_FILENAME'] # path to ssh key
    env.ec2_key_pair = os.environ['AWS_KEY_PAIR']
    env.ec2_region = os.getenv('AWS_REGION', 'us-west-2')
    env.ec2_secret = os.environ['AWS_SECRET_ACCESS_KEY']
    env.ec2_security = ['data-platform']
    env.user = 'centos'
    env.disable_known_hosts = True
    env.key_filename = env.ec2_key_filename

    if is_there_state() is False:
        pass
    else:
        data = load_state_from_disk()
        env.hosts = data['ip_address']


main()
