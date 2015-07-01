Code for deploying the data-platform

Usage:
------

|
|
1. Set the following environment variables

|
|

   * AWS_INSTANCE_TYPE (defaults to t2.micro)

   * AWS_KEY_PAIR (the EC2 key_pair used for your ec2 instance)

   * AWS_AMI (defaults to ami-c7d092f7)

   * AWS_KEY_FILENAME (this is the full path to your ssh private key file)

   * AWS_SECRET_ACCESS_KEY

   * AWS_REGION (defaults to us-west-2)

   * AWS_ACCESS_KEY_ID
|
|
2. initialize a VirtualEnv:

.. code-block:: bash

    $ virtualenv2 venv # it might be named 'virtualenv' in some distributions
    $ source venv/bin/activate
    $ pip2 install -r requirements.txt

|
|

3. Bring the box up

.. code-block:: bash

    $ fab it
|
|


4. ssh to it

.. code-block:: bash

    $ fab ssh
    $ fab ssh:'ls -l'
|
|


5. details about the EC2 instance are available through:

.. code-block:: bash

    $ fab status
|
|


6. When done:

.. code-block:: bash

    $ fab destroy
|
|

